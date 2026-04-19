"""Thin wrapper that turns a Job payload into a call to session_runner.run_session
and builds a JobResult from the return dict.

Device selection:
  - Job.device_id     → lookup in active_devices.json (logical pool id)
  - Job.device_serial → use the serial directly, bypass the pool entirely
  - neither           → auto-pick first free entry from active_devices.json

In-flight tracking is keyed by **resolved serial**, so a Job using device_id
and a Job using the matching raw serial collide correctly.

When Job.proxy is set, the runner spins up a per-Job gost listener on a
dynamically allocated port and tears it down when the Job finishes.

See docs/EXECUTOR_PAYLOAD.md for the authoritative Job/JobResult schema.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from gost_manager import GostManager
from session_runner import run_session


ACTIVE_DEVICES_PATH = os.path.join(PROJECT_ROOT, "active_devices.json")

GOST_PORT_MIN = 11001
GOST_PORT_MAX = 12000

# Brands that must use ADB-only (Appium crashes or hangs on these).
# Lower-cased for case-insensitive match against ro.product.brand.
ADB_ONLY_BRANDS = {"infinix", "tecno"}

DEFAULT_APPIUM_PORT = 4723


# ── Exceptions surfaced to the HTTP layer ─────────────────────────────────────


class DeviceBusy(Exception):
    """Raised when a Job targets a device that's already running a Job."""
    def __init__(self, identifier: str, holder_job_id: str) -> None:
        super().__init__(f"device_busy: {identifier} (in use by {holder_job_id})")
        self.identifier = identifier
        self.holder_job_id = holder_job_id


class NoFreeDevice(Exception):
    """Raised when auto-pick can't find a device not already running a Job."""


class DeviceNotInPool(Exception):
    """Raised when Job.device_id doesn't match any entry in active_devices.json."""


class DeviceUnreachable(Exception):
    """Raised when Job.device_serial targets a phone that ADB can't talk to."""


# ── Shared state (thread-safe) ────────────────────────────────────────────────


# Keyed by resolved full_serial so both device_id and device_serial paths
# collide correctly. Value: job_id currently holding the phone.
_serials_in_use: Dict[str, Dict[str, str]] = {}
_serials_lock = threading.Lock()

_ports_in_use: set = set()
_ports_lock = threading.Lock()


def _allocate_port() -> int:
    """Pick the lowest free gost port in [GOST_PORT_MIN, GOST_PORT_MAX]. Also
    probes the OS — catches ports held by non-executor processes."""
    with _ports_lock:
        for p in range(GOST_PORT_MIN, GOST_PORT_MAX + 1):
            if p in _ports_in_use:
                continue
            if _port_bindable(p):
                _ports_in_use.add(p)
                return p
    raise RuntimeError(f"no free gost port in [{GOST_PORT_MIN}, {GOST_PORT_MAX}]")


def _release_port(port: int) -> None:
    with _ports_lock:
        _ports_in_use.discard(port)


def _port_bindable(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


# ── Device pool (optional — only used for device_id path + auto-pick) ────────


def _load_device_pool() -> Dict[str, Dict[str, Any]]:
    """Load active_devices.json. Returns {} if the file is missing — the
    scheduler can still drive the executor via device_serial without a pool."""
    if not os.path.exists(ACTIVE_DEVICES_PATH):
        return {}
    try:
        with open(ACTIVE_DEVICES_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def device_pool_size() -> int:
    return len(_load_device_pool())


def devices_available() -> int:
    with _serials_lock:
        busy = len(_serials_in_use)
    return max(0, device_pool_size() - busy)


def in_flight_jobs() -> List[Dict[str, str]]:
    with _serials_lock:
        return [
            {
                "device_id":     info.get("device_id") or "",
                "device_serial": serial,
                "job_id":        info["job_id"],
            }
            for serial, info in _serials_in_use.items()
        ]


# ── Serial / device resolution ────────────────────────────────────────────────


def _adb_reachable(serial: str) -> bool:
    """Quick `adb -s <serial> shell echo ok` probe."""
    try:
        r = subprocess.run(
            ["adb", "-s", serial, "shell", "echo", "ok"],
            capture_output=True, text=True, timeout=5,
        )
        return "ok" in r.stdout
    except Exception:
        return False


def _detect_use_adb(serial: str) -> bool:
    """Read ro.product.brand from the device. Infinix/TECNO → True (ADB-only).
    All other brands default to False (Appium). Returns True on failure to
    match session_runner's default (currently use_adb=True for all active
    pool devices)."""
    try:
        r = subprocess.run(
            ["adb", "-s", serial, "shell", "getprop", "ro.product.brand"],
            capture_output=True, text=True, timeout=5,
        )
        brand = r.stdout.strip().lower()
        return brand in ADB_ONLY_BRANDS or not brand
    except Exception:
        return True


def _derive_short_serial(full_serial: str) -> str:
    """`adb-c0897ffc-XYZ._adb-tls-connect._tcp` → `c0897ffc`. Falls back to
    full_serial if the transport shape is unexpected."""
    if full_serial.startswith("adb-") and "_adb-tls-connect" in full_serial:
        inner = full_serial[4:].split("._adb-tls-connect")[0]
        return inner.rsplit("-", 1)[0]
    return full_serial


def _resolve_device(device_id_hint: Optional[str],
                    device_serial_hint: Optional[str],
                    job_id: str) -> Tuple[Optional[str], str, Dict[str, Any]]:
    """Return (device_id_or_None, full_serial, dev_info) and atomically mark
    the serial as in-flight.

    Three code paths:
      1. device_id given        → pool lookup, use pool's serial/port/use_adb
      2. device_serial given    → skip pool, auto-detect use_adb/port from device
      3. both None (auto-pick)  → first free pool entry
    """
    pool = _load_device_pool()

    # ── Path 2: direct serial, no pool needed ───────────────────────────────
    if device_serial_hint:
        full_serial = device_serial_hint
        if not _adb_reachable(full_serial):
            raise DeviceUnreachable(
                f"device_unreachable: adb cannot reach {full_serial!r}"
            )

        # Match back to a pool entry if one exists (so device_id is still
        # echoed in the JobResult when possible)
        matched_id: Optional[str] = None
        matched_info: Optional[Dict[str, Any]] = None
        for did, info in pool.items():
            if info.get("serial") == full_serial:
                matched_id = did
                matched_info = info
                break

        if matched_info is not None:
            dev_info = matched_info
        else:
            dev_info = {
                "serial":  full_serial,
                "port":    DEFAULT_APPIUM_PORT,
                "use_adb": _detect_use_adb(full_serial),
            }

        with _serials_lock:
            if full_serial in _serials_in_use:
                raise DeviceBusy(full_serial, _serials_in_use[full_serial]["job_id"])
            _serials_in_use[full_serial] = {
                "job_id":    job_id,
                "device_id": matched_id or "",
            }
        return matched_id, full_serial, dev_info

    # ── Path 1: logical pool id ──────────────────────────────────────────────
    if device_id_hint:
        if not pool:
            raise NoFreeDevice(
                "active_devices.json is missing or empty — pass device_serial "
                "or run setup_devices.py --assign"
            )
        if device_id_hint not in pool:
            raise DeviceNotInPool(
                f"device_id {device_id_hint!r} not in pool (have: {list(pool.keys())})"
            )
        info = pool[device_id_hint]
        full_serial = info["serial"]

        with _serials_lock:
            if full_serial in _serials_in_use:
                raise DeviceBusy(
                    device_id_hint, _serials_in_use[full_serial]["job_id"]
                )
            _serials_in_use[full_serial] = {
                "job_id":    job_id,
                "device_id": device_id_hint,
            }
        return device_id_hint, full_serial, info

    # ── Path 3: auto-pick ────────────────────────────────────────────────────
    if not pool:
        raise NoFreeDevice(
            "active_devices.json is missing or empty and no device_serial "
            "was passed — nothing to auto-pick from"
        )
    with _serials_lock:
        for did, info in pool.items():
            full_serial = info["serial"]
            if full_serial in _serials_in_use:
                continue
            _serials_in_use[full_serial] = {"job_id": job_id, "device_id": did}
            return did, full_serial, info
        raise NoFreeDevice(f"all {len(pool)} devices currently in use")


def _release_serial(full_serial: str) -> None:
    with _serials_lock:
        _serials_in_use.pop(full_serial, None)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _proxy_dict_from_job(proxy) -> Optional[Dict[str, Any]]:
    if proxy is None:
        return None
    if hasattr(proxy, "model_dump"):
        return proxy.model_dump(exclude_none=True)
    return dict(proxy)


def _build_proxy_resolved(result_proxy: Optional[Dict[str, Any]],
                          gost_endpoint: Optional[str]) -> Optional[Dict[str, Any]]:
    if not result_proxy and not gost_endpoint:
        return None
    info = result_proxy or {}
    return {
        "status":           info.get("status", "UNKNOWN"),
        "gost_endpoint":    gost_endpoint,
        "upstream_session": info.get("username"),
        "exit_ip":          info.get("ip"),
        "exit_city":        info.get("ip_city"),
        "exit_region":      info.get("ip_region"),
        "exit_zip":         info.get("ip_zip"),
        "mocked_latitude":  info.get("mocked_latitude"),
        "mocked_longitude": info.get("mocked_longitude"),
        "mocked_timezone":  info.get("mocked_timezone"),
    }


def _iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _error_result(job: Dict[str, Any], started_at: str, t0: float,
                  device_id: Optional[str], device_serial: Optional[str],
                  error: str) -> Dict[str, Any]:
    return {
        **job,
        "device_id":        device_id,
        "device_serial":    device_serial,
        "status":           "error",
        "error":            error,
        "started_at":       started_at,
        "finished_at":      _iso_utc(),
        "duration_s":       round(time.time() - t0, 2),
        "proxy_resolved":   None,
        "response_preview": None,
        "backlink_clicked": None,
        "steps":            [],
    }


# ── Main entry point ──────────────────────────────────────────────────────────


def execute_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """Run one Job and return a JobResult dict.

    Raises DeviceBusy / NoFreeDevice / DeviceNotInPool / DeviceUnreachable
    up to the route layer (which maps them to HTTP 409/400/503).
    Any other failure is captured inside the result with status='error'.
    """
    started_at = _iso_utc()
    t0 = time.time()

    device_id, full_serial, dev = _resolve_device(
        device_id_hint     = job.get("device_id"),
        device_serial_hint = job.get("device_serial"),
        job_id             = job["job_id"],
    )

    short_serial  = _derive_short_serial(full_serial)
    port          = dev.get("port", DEFAULT_APPIUM_PORT)
    use_adb       = dev.get("use_adb", True)

    # Stable label for log lines + gost device spec key. Uses device_id if we
    # have one, else a short derivative of the serial.
    device_label  = device_id or short_serial

    proxy_config  = _proxy_dict_from_job(job.get("proxy"))
    gost_endpoint: Optional[str] = None
    gost_port:     Optional[int] = None
    gm:            Optional[GostManager] = None

    try:
        # ── Per-Job gost when proxy is requested ─────────────────────────────
        if proxy_config:
            try:
                gost_port = _allocate_port()
            except Exception as e:
                return _error_result(job, started_at, t0, device_id, full_serial,
                                     f"gost_port_alloc_failed: {e}")

            gm_spec = [{
                "device_id":        device_label,
                "zip":              proxy_config.get("zip", "10001"),
                "country":          proxy_config.get("country", "us"),
                "session_duration": int(proxy_config.get("session_duration", 30)),
            }]
            try:
                gm = GostManager(gm_spec, base_port=gost_port)
                gm.start()
            except Exception as e:
                _release_port(gost_port)
                return _error_result(job, started_at, t0, device_id, full_serial,
                                     f"gost_start_failed: {e}")

            mapping = gm.mapping[device_label]
            gost_endpoint = f"{mapping['host']}:{mapping['port']}"
            proxy_config["gost"] = mapping

        # ── Build sess_meta and delegate ─────────────────────────────────────
        sess_meta = {
            "use_adb":   use_adb,
            "backlinks": job.get("backlinks") or [],
            "proxy":     proxy_config,
            "platforms": [job["platform"]],
        }

        try:
            result = run_session(
                serial      = short_serial,
                full_serial = full_serial,
                port        = port,
                platform    = job["platform"],
                prompt      = job["prompt"],
                follow_up   = job.get("follow_up"),
                device_id   = device_label,
                sess_meta   = sess_meta,
            )
        except Exception as e:
            return _error_result(job, started_at, t0, device_id, full_serial,
                                 f"runner_exception: {type(e).__name__}: {e}")

        success = bool(result.get("success"))

        return {
            **job,
            "device_id":        device_id,
            "device_serial":    full_serial,
            "status":           "success" if success else "error",
            "error":            None if success else (result.get("error") or "unknown_error"),
            "started_at":       started_at,
            "finished_at":      _iso_utc(),
            "duration_s":       round(time.time() - t0, 2),
            "proxy_resolved":   _build_proxy_resolved(result.get("proxy"), gost_endpoint),
            "response_preview": result.get("response_preview"),
            "backlink_clicked": result.get("backlink_clicked"),
            "steps":            result.get("steps") or [],
        }

    finally:
        if gm is not None:
            try:
                gm.stop()
            except Exception as e:
                print(f"[executor] gost stop error: {e}")
        if gost_port is not None:
            _release_port(gost_port)
        _release_serial(full_serial)
