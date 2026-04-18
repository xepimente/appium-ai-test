"""Thin wrapper that turns a Job payload into a call to session_runner.run_session
and builds a JobResult from the return dict.

Concurrency model:
  - Multiple Jobs can run simultaneously (one per device)
  - Per-device in-flight tracking rejects a 2nd Job on a busy device with a
    structured `DeviceBusy` error; scheduler is expected to retry elsewhere
  - When a Job has `proxy`, the runner spins up a per-Job gost listener on a
    dynamically allocated port and tears it down on finish (matches how
    run_parallel yesterday produced 5/5 PASS)

See docs/EXECUTOR_PAYLOAD.md for the authoritative Job/JobResult schema.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from gost_manager import GostManager, detect_lan_ip
from session_runner import run_session


ACTIVE_DEVICES_PATH = os.path.join(PROJECT_ROOT, "active_devices.json")

GOST_PORT_MIN = 11001
GOST_PORT_MAX = 12000


# ── Exceptions surfaced to the HTTP layer ─────────────────────────────────────


class DeviceBusy(Exception):
    """Raised when a Job targets a device that's already running a Job."""
    def __init__(self, device_id: str, holder_job_id: str) -> None:
        super().__init__(f"device_busy: {device_id} (in use by {holder_job_id})")
        self.device_id = device_id
        self.holder_job_id = holder_job_id


class NoFreeDevice(Exception):
    """Raised when auto-pick can't find a device not already running a Job."""


class DeviceNotInPool(Exception):
    """Raised when Job.device_id doesn't match any entry in active_devices.json."""


# ── Shared state (thread-safe) ────────────────────────────────────────────────


_devices_in_use: Dict[str, str] = {}   # device_id → job_id currently holding it
_devices_lock = threading.Lock()

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


# ── Device pool ───────────────────────────────────────────────────────────────


def _load_device_pool() -> Dict[str, Dict[str, Any]]:
    with open(ACTIVE_DEVICES_PATH) as f:
        return json.load(f)


def device_pool_size() -> int:
    try:
        return len(_load_device_pool())
    except Exception:
        return 0


def devices_available() -> int:
    with _devices_lock:
        in_use = set(_devices_in_use.keys())
    return max(0, device_pool_size() - len(in_use))


def in_flight_jobs() -> List[Dict[str, str]]:
    """Return a snapshot of currently-running jobs keyed by device_id."""
    with _devices_lock:
        return [{"device_id": did, "job_id": jid} for did, jid in _devices_in_use.items()]


def _claim_device(device_id_hint: Optional[str], job_id: str) -> Tuple[str, Dict[str, Any]]:
    """Pick a device and mark it in-flight atomically. Raises DeviceBusy /
    NoFreeDevice / DeviceNotInPool on failure."""
    pool = _load_device_pool()
    if not pool:
        raise NoFreeDevice("active_devices.json is empty")

    with _devices_lock:
        if device_id_hint:
            if device_id_hint not in pool:
                raise DeviceNotInPool(
                    f"device_id {device_id_hint!r} not in pool (have: {list(pool.keys())})"
                )
            if device_id_hint in _devices_in_use:
                raise DeviceBusy(device_id_hint, _devices_in_use[device_id_hint])
            _devices_in_use[device_id_hint] = job_id
            return device_id_hint, pool[device_id_hint]

        # Auto-pick: first pool entry not currently in use
        for did, info in pool.items():
            if did not in _devices_in_use:
                _devices_in_use[did] = job_id
                return did, info
        raise NoFreeDevice(f"all {len(pool)} devices currently in use")


def _release_device(device_id: str) -> None:
    with _devices_lock:
        _devices_in_use.pop(device_id, None)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _derive_short_serial(full_serial: str) -> str:
    """`adb-c0897ffc-XYZ._adb-tls-connect._tcp` → `c0897ffc`. Falls back to
    full_serial if the transport shape is unexpected."""
    if full_serial.startswith("adb-") and "_adb-tls-connect" in full_serial:
        inner = full_serial[4:].split("._adb-tls-connect")[0]
        return inner.rsplit("-", 1)[0]
    return full_serial


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

    Raises DeviceBusy / NoFreeDevice / DeviceNotInPool up to the route layer
    (which maps them to 409/400). Any other failure is captured inside the
    result with status='error' + a descriptive error code.
    """
    started_at = _iso_utc()
    t0 = time.time()

    # Claim a device (raises exceptions the route handler surfaces as HTTP errors)
    device_id, dev = _claim_device(job.get("device_id"), job["job_id"])

    full_serial   = dev["serial"]
    short_serial  = _derive_short_serial(full_serial)
    port          = dev.get("port", 0)
    use_adb       = dev.get("use_adb", True)

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
                "device_id":        device_id,
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

            mapping = gm.mapping[device_id]
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
                device_id   = device_id,
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
        _release_device(device_id)
