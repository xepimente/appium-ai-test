"""
gost proxy manager — spins up N local SOCKS5 listeners on the Mac, each chained
to a fresh Decodo upstream session. Devices point their SocksDroid at the Mac's
LAN IP + the assigned listener port (no WAN tunnel on the phones themselves).

Why: when each phone holds its own WAN Decodo tunnel on shared WiFi, the tun0
interface flickers under parallel load — VPN drops, ADB type_text drops chars,
Chrome shows "site cannot be reached". Moving the SOCKS5 layer onto the Mac
gives each phone a stable LAN socket; only the Mac holds the upstream tunnels.

Usage:
    from gost_manager import GostManager

    devices = [
        {"device_id": "device-101", "zip": "32504"},
        {"device_id": "device-102", "zip": "32504"},
        ...
    ]
    with GostManager(devices) as gm:
        # gm.mapping[device_id] -> {"host": "192.168.0.102", "port": 11001,
        #                           "username": "...", "password": "..."}
        ...
"""
from __future__ import annotations

import os
import random
import shutil
import socket
import string
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

def _load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

PROXY_HOST     = os.environ.get("PROXY_HOST", "gate.decodo.com")
PROXY_PORT     = int(os.environ.get("PROXY_PORT", "10001"))
PROXY_BASE_USER = os.environ.get("PROXY_BASE_USER", "user-spknlt0736")
PROXY_PASSWORD  = os.environ.get("PROXY_PASSWORD", "")

GOST_BINARY = shutil.which("gost") or "/opt/homebrew/bin/gost"

DEFAULT_COUNTRY          = "us"
DEFAULT_SESSION_DURATION = 30
DEFAULT_BASE_PORT        = 11001


def _random_session_id(n: int = 10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def detect_lan_ip() -> str:
    """Return the Mac's primary LAN IP (the one reachable by phones on WiFi)."""
    for iface in ("en0", "en1"):
        try:
            out = subprocess.check_output(["ipconfig", "getifaddr", iface], text=True, timeout=3).strip()
            if out:
                return out
        except Exception:
            continue
    # UDP socket trick — opens no actual socket, just asks kernel which IP would be used
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def build_upstream_username(
    zip_code: str,
    country: str = DEFAULT_COUNTRY,
    session_duration: int = DEFAULT_SESSION_DURATION,
    session_id: Optional[str] = None,
) -> str:
    sid = session_id or _random_session_id()
    return (
        f"{PROXY_BASE_USER}"
        f"-session-{sid}"
        f"-sessionduration-{session_duration}"
        f"-country-{country}"
        f"-zip-{zip_code}"
    )


@dataclass(frozen=True)
class ListenerSpec:
    device_id: str
    port: int
    zip_code: str
    country: str
    session_duration: int
    session_id: str
    upstream_user: str

    @property
    def host(self) -> str:
        return detect_lan_ip()


class GostManager:
    """Lifecycle wrapper around a single gost process that fans out N listeners."""

    def __init__(
        self,
        devices: List[Dict],
        base_port: int = DEFAULT_BASE_PORT,
        listener_user: str = "anon",
        listener_pass: str = "anon",
        debug: bool = False,
    ) -> None:
        if not devices:
            raise ValueError("GostManager requires at least one device spec")
        if not os.path.exists(GOST_BINARY):
            raise RuntimeError(
                f"gost binary not found at {GOST_BINARY}. Install with `brew install gost`."
            )
        if not PROXY_PASSWORD:
            raise RuntimeError("PROXY_PASSWORD is not set (check .env)")

        self.listener_user = listener_user
        self.listener_pass = listener_pass
        self.debug         = debug
        self.process: Optional[subprocess.Popen] = None
        self.specs: List[ListenerSpec] = []
        self.mapping: Dict[str, Dict] = {}
        self._config_path: Optional[str] = None

        lan_ip = detect_lan_ip()

        for i, dev in enumerate(devices):
            port = base_port + i
            zip_code = str(dev.get("zip") or "10001")
            country  = dev.get("country", DEFAULT_COUNTRY)
            duration = int(dev.get("session_duration", DEFAULT_SESSION_DURATION))
            sid      = dev.get("session_id") or _random_session_id()
            upstream = build_upstream_username(zip_code, country, duration, sid)
            spec = ListenerSpec(
                device_id=dev["device_id"],
                port=port,
                zip_code=zip_code,
                country=country,
                session_duration=duration,
                session_id=sid,
                upstream_user=upstream,
            )
            self.specs.append(spec)
            self.mapping[spec.device_id] = {
                "host":       lan_ip,
                "port":       spec.port,
                "username":   listener_user,
                "password":   listener_pass,
                "session_id": sid,
                "zip":        zip_code,
            }

    def _build_yaml_config(self) -> str:
        """gost v3 CLI pools all -F into one hop pool (round-robin) — breaking
        per-listener session pinning. Use YAML config to assign each service its
        own chain with its own Decodo session."""
        lines: List[str] = ["services:"]
        for i, spec in enumerate(self.specs):
            lines += [
                f"  - name: service-{i}",
                f'    addr: ":{spec.port}"',
                f"    handler:",
                f"      type: socks5",
                f"      chain: chain-{i}",
                f"      auth:",
                f'        username: "{self.listener_user}"',
                f'        password: "{self.listener_pass}"',
                f"    listener:",
                f"      type: tcp",
            ]
        lines.append("chains:")
        for i, spec in enumerate(self.specs):
            lines += [
                f"  - name: chain-{i}",
                f"    hops:",
                f"      - name: hop-{i}",
                f"        nodes:",
                f"          - name: decodo-{i}",
                f"            addr: {PROXY_HOST}:{PROXY_PORT}",
                f"            connector:",
                f"              type: socks5",
                f"              auth:",
                f'                username: "{spec.upstream_user}"',
                f'                password: "{PROXY_PASSWORD}"',
                f"            dialer:",
                f"              type: tcp",
            ]
        return "\n".join(lines) + "\n"

    def start(self, wait_seconds: float = 1.5) -> None:
        """Launch gost. Raises if it dies within wait_seconds."""
        yaml = self._build_yaml_config()
        cfg = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", prefix="gost_", delete=False,
        )
        cfg.write(yaml)
        cfg.close()
        self._config_path = cfg.name

        print(f"[gost] Starting {len(self.specs)} listener(s) (config={cfg.name}):")
        for spec in self.specs:
            print(
                f"  {spec.device_id}: 0.0.0.0:{spec.port} → "
                f"session={spec.session_id} zip={spec.zip_code}"
            )
        args = [GOST_BINARY, "-C", cfg.name]
        if self.debug:
            args.append("-D")
        self.process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL if not self.debug else None,
            stderr=subprocess.STDOUT if not self.debug else None,
        )
        time.sleep(wait_seconds)
        if self.process.poll() is not None:
            rc = self.process.returncode
            self.process = None
            raise RuntimeError(f"gost exited early with code {rc}")
        # Sanity: verify each listener is bound locally
        for spec in self.specs:
            if not self._port_listening(spec.port):
                self.stop()
                raise RuntimeError(f"gost listener on port {spec.port} is not accepting connections")
        print(f"[gost] All {len(self.specs)} listener(s) up (pid={self.process.pid}).")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
            print(f"[gost] Stopped (pid was {self.process.pid}).")
        self.process = None
        if getattr(self, "_config_path", None):
            try:
                os.unlink(self._config_path)
            except OSError:
                pass
            self._config_path = None

    @staticmethod
    def _port_listening(port: int) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            result = s.connect_ex(("127.0.0.1", port))
            return result == 0
        finally:
            s.close()

    def __enter__(self) -> "GostManager":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def self_test() -> int:
    """Quick end-to-end check: spin up 1 listener for zip 32504 and verify
    the upstream IP geolocates to Pensacola FL.
    """
    import urllib.request
    import json

    print("[self-test] starting single-listener smoke test...")
    gm = GostManager([{"device_id": "selftest", "zip": "32504"}], debug=False)
    try:
        gm.start()
        mapping = gm.mapping["selftest"]
        proxy_url = f"socks5h://{mapping['username']}:{mapping['password']}@127.0.0.1:{mapping['port']}"
        print(f"[self-test] proxy_url: {proxy_url}")
        # Use curl via subprocess for SOCKS5 (urllib doesn't support socks5 out of the box)
        out = subprocess.check_output(
            [
                "curl", "-s", "--max-time", "20",
                "--socks5", f"127.0.0.1:{mapping['port']}",
                "-U", f"{mapping['username']}:{mapping['password']}",
                "https://ipinfo.io/json",
            ],
            text=True, timeout=30,
        )
        data = json.loads(out)
        print(f"[self-test] ip={data.get('ip')}  city={data.get('city')}  "
              f"region={data.get('region')}  postal={data.get('postal')}")
        ok = data.get("region") == "Florida"
        print(f"[self-test] {'PASS' if ok else 'FAIL — expected Florida'}")
        return 0 if ok else 2
    finally:
        gm.stop()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        sys.exit(self_test())
    print("Usage: python3 gost_manager.py test")
    sys.exit(1)
