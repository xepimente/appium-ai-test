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
            v = v.strip()
            # Strip matching surrounding quotes — .env values like
            # PROXY_PASSWORD='secret' would otherwise carry literal quotes into
            # the gost connector auth and break Decodo authentication.
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            os.environ.setdefault(k.strip(), v)


_load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

PROXY_HOST     = os.environ.get("PROXY_HOST", "gate.decodo.com")
PROXY_PORT     = int(os.environ.get("PROXY_PORT", "10001"))
PROXY_BASE_USER = os.environ.get("PROXY_BASE_USER", "user-spknlt0736")
PROXY_PASSWORD  = os.environ.get("PROXY_PASSWORD", "")
# Which upstream residential provider's username scheme to build. "decodo" (default)
# uses -session-<sid>-...-country-us[-zip-X]; "dataimpulse" uses the __cr.us[__region.X]
# __sid.<sid> suffix scheme. DataImpulse has NO zip targeting — state (region) is the
# finest tier, so zip-tier probing is skipped for it.
PROXY_PROVIDER  = os.environ.get("PROXY_PROVIDER", "decodo").lower()

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


_STATE_CODE_TO_REGION = {
    "al": "alabama",       "ak": "alaska",        "az": "arizona",        "ar": "arkansas",
    "ca": "california",    "co": "colorado",      "ct": "connecticut",    "de": "delaware",
    "fl": "florida",       "ga": "georgia",       "hi": "hawaii",         "id": "idaho",
    "il": "illinois",      "in": "indiana",       "ia": "iowa",           "ks": "kansas",
    "ky": "kentucky",      "la": "louisiana",     "me": "maine",          "md": "maryland",
    "ma": "massachusetts", "mi": "michigan",      "mn": "minnesota",      "ms": "mississippi",
    "mo": "missouri",      "mt": "montana",       "ne": "nebraska",       "nv": "nevada",
    "nh": "new_hampshire", "nj": "new_jersey",    "nm": "new_mexico",     "ny": "new_york",
    "nc": "north_carolina","nd": "north_dakota",  "oh": "ohio",           "ok": "oklahoma",
    "or": "oregon",        "pa": "pennsylvania",  "ri": "rhode_island",   "sc": "south_carolina",
    "sd": "south_dakota",  "tn": "tennessee",     "tx": "texas",          "ut": "utah",
    "vt": "vermont",       "va": "virginia",      "wa": "washington",     "wv": "west_virginia",
    "wi": "wisconsin",     "wy": "wyoming",       "dc": "district_of_columbia",
}


def resolve_region(state_or_region: str) -> str:
    """Map US state code ('oh') or full name ('ohio', 'New York') to the
    Decodo region keyword ('ohio', 'new_york'). Returns '' for empty input."""
    s = (state_or_region or "").strip().lower().replace(" ", "_")
    if not s:
        return ""
    return _STATE_CODE_TO_REGION.get(s, s)


def build_upstream_username(
    zip_code: str = "",
    country: str = DEFAULT_COUNTRY,
    session_duration: int = DEFAULT_SESSION_DURATION,
    session_id: Optional[str] = None,
    region: str = "",
) -> str:
    """Build Decodo sticky-session username. Targeting precedence:
    zip (if given) else region (if given) else country-only.
    Decodo keywords used: country, zip, region (full state name, underscores).

    DataImpulse scheme (PROXY_PROVIDER=dataimpulse): __cr.<country>[__region.<state>]
    __sid.<sid>. DataImpulse has no zip targeting, so zip_code is ignored and state
    (region) is the finest tier; region is emitted with underscores stripped."""
    sid = session_id or _random_session_id()
    if PROXY_PROVIDER == "dataimpulse":
        di = [f"{PROXY_BASE_USER}__cr.{country}"]
        if region:
            di.append(f"__region.{region.replace('_', '')}")
        di.append(f"__sid.{sid}")
        return "".join(di)
    parts = [
        PROXY_BASE_USER,
        f"session-{sid}",
        f"sessionduration-{session_duration}",
        f"country-{country}",
    ]
    if zip_code:
        parts.append(f"zip-{zip_code}")
    elif region:
        parts.append(f"region-{region}")
    return "-".join(parts)


def _probe_decodo_upstream(username: str, timeout: float = 10.0, attempts: int = 2) -> bool:
    """Probe Decodo with a candidate sticky-session username. Returns True
    when Decodo successfully relays an IP-check request. Retries once with
    backoff to smooth over transient SOCKS5 rate-limit RSTs when many probes
    fire in quick succession. False means real no-coverage."""
    import re
    proxy = f"socks5h://{username}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}"
    for attempt in range(attempts):
        try:
            r = subprocess.run(
                ["curl", "-sS", "--max-time", str(int(timeout)),
                 "--proxy", proxy, "https://ifconfig.me/ip"],
                capture_output=True, text=True, timeout=timeout + 2,
            )
            out = (r.stdout or "").strip()
            if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", out):
                return True
        except Exception:
            pass
        if attempt + 1 < attempts:
            time.sleep(1.5)
    return False


@dataclass(frozen=True)
class ListenerSpec:
    device_id: str
    port: int
    zip_code: str
    region: str
    country: str
    session_duration: int
    session_id: str
    upstream_user: str
    upstream_port: int = 0  # 0 = use global PROXY_PORT + session-in-username
    tier: str = "zip"

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
            region   = resolve_region(dev.get("state") or dev.get("region") or "")
            country  = dev.get("country", DEFAULT_COUNTRY)
            duration = int(dev.get("session_duration", DEFAULT_SESSION_DURATION))
            sid      = dev.get("session_id") or _random_session_id()
            # Port-based mode: dev may specify an upstream_port (e.g. 10003-10007
            # from Decodo's endpoint generator). Each port is its own sticky IP,
            # so we use the plain base username — no session-id or zip needed.
            upstream_port_override = int(dev.get("upstream_port") or 0)
            if upstream_port_override:
                upstream = PROXY_BASE_USER
                initial_tier = "port"
            else:
                upstream = build_upstream_username(zip_code, country, duration, sid)
                initial_tier = "zip"
            spec = ListenerSpec(
                device_id=dev["device_id"],
                port=port,
                zip_code=zip_code,
                region=region,
                country=country,
                session_duration=duration,
                session_id=sid,
                upstream_user=upstream,
                upstream_port=upstream_port_override,
                tier=initial_tier,
            )
            self.specs.append(spec)
            self.mapping[spec.device_id] = {
                "host":       lan_ip,
                "port":       spec.port,
                "username":   listener_user,
                "password":   listener_pass,
                "session_id": sid,
                "zip":        zip_code,
                "region":     region,
                "tier":       initial_tier,
                "upstream_port": upstream_port_override or PROXY_PORT,
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
            upstream_port = spec.upstream_port or PROXY_PORT
            lines += [
                f"  - name: chain-{i}",
                f"    hops:",
                f"      - name: hop-{i}",
                f"        nodes:",
                f"          - name: decodo-{i}",
                f"            addr: {PROXY_HOST}:{upstream_port}",
                f"            connector:",
                f"              type: socks5",
                f"              auth:",
                f'                username: "{spec.upstream_user}"',
                f'                password: "{PROXY_PASSWORD}"',
                f"            dialer:",
                f"              type: tcp",
            ]
        return "\n".join(lines) + "\n"

    def _preflight_upstreams(self) -> None:
        """Probe Decodo for each spec and degrade targeting where needed.

        Decodo RSTs SOCKS connections for zip codes with no residential
        coverage (e.g. small US towns). Falls back: zip -> state -> country.
        Updates specs + mapping with the chosen tier's upstream_user."""
        from dataclasses import replace

        print(f"[gost] Preflight upstream targeting for {len(self.specs)} spec(s)…")
        new_specs: List[ListenerSpec] = []
        for spec in self.specs:
            # Port-based specs skip preflight — Decodo's port endpoint IS the
            # session; no zip targeting to probe or fall back from.
            if spec.tier == "port":
                print(f"[gost]   {spec.device_id}: tier=port (upstream :{spec.upstream_port}) — no probe needed")
                new_specs.append(spec)
                continue
            # DataImpulse: no zip targeting. State (region) is the finest tier —
            # probe it if known (verifies the key authenticates), else country.
            if PROXY_PROVIDER == "dataimpulse":
                if spec.region:
                    region_probe = build_upstream_username(
                        country=spec.country, session_id=_random_session_id(),
                        region=spec.region,
                    )
                    picked_tier = "region" if _probe_decodo_upstream(region_probe) else "country"
                else:
                    picked_tier = "country"
                picked_user = build_upstream_username(
                    country=spec.country, session_id=spec.session_id,
                    region=(spec.region if picked_tier == "region" else ""),
                )
                time.sleep(0.6)
                print(f"[gost]   {spec.device_id}: dataimpulse region={spec.region or '-'} "
                      f"→ using tier={picked_tier}")
                new_specs.append(replace(spec, upstream_user=picked_user, tier=picked_tier))
                self.mapping[spec.device_id]["tier"] = picked_tier
                continue
            # Probe zip only. If zip RSTs, fall back without further probing
            # (region is valid when state is known; country always works). Extra
            # probes risk Decodo rate-limiting our real listener upstream.
            probe_sid = _random_session_id()
            zip_user = build_upstream_username(
                zip_code=spec.zip_code, country=spec.country,
                session_duration=spec.session_duration, session_id=probe_sid,
            )
            if spec.zip_code and _probe_decodo_upstream(zip_user):
                picked_tier = "zip"
            elif spec.region:
                picked_tier = "region"
            else:
                picked_tier = "country"

            def _user_for(tier: str, sid: str) -> str:
                if tier == "zip":
                    return build_upstream_username(
                        zip_code=spec.zip_code, country=spec.country,
                        session_duration=spec.session_duration, session_id=sid,
                    )
                if tier == "region":
                    return build_upstream_username(
                        country=spec.country, session_duration=spec.session_duration,
                        session_id=sid, region=spec.region,
                    )
                return build_upstream_username(
                    country=spec.country, session_duration=spec.session_duration,
                    session_id=sid,
                )

            picked_user = _user_for(picked_tier, spec.session_id)
            time.sleep(0.6)  # space out probes between specs to ease Decodo rate limit

            if picked_tier == "zip":
                print(f"[gost]   {spec.device_id}: tier=zip zip={spec.zip_code} OK")
            else:
                print(f"[gost]   {spec.device_id}: zip={spec.zip_code or '-'} "
                      f"region={spec.region or '-'} → using tier={picked_tier}")
            new_specs.append(replace(spec, upstream_user=picked_user, tier=picked_tier))
            self.mapping[spec.device_id]["tier"] = picked_tier
        self.specs = new_specs

    def start(self, wait_seconds: float = 1.5) -> None:
        """Launch gost. Raises if it dies within wait_seconds."""
        self._preflight_upstreams()
        yaml = self._build_yaml_config()
        cfg = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", prefix="gost_", delete=False,
        )
        cfg.write(yaml)
        cfg.close()
        self._config_path = cfg.name

        print(f"[gost] Starting {len(self.specs)} listener(s) (config={cfg.name}):")
        for spec in self.specs:
            upstream_port = spec.upstream_port or PROXY_PORT
            if spec.tier == "port":
                print(f"  {spec.device_id}: 0.0.0.0:{spec.port} → {PROXY_HOST}:{upstream_port} (port-based)")
            else:
                print(
                    f"  {spec.device_id}: 0.0.0.0:{spec.port} → {PROXY_HOST}:{upstream_port} "
                    f"session={spec.session_id} zip={spec.zip_code}"
                )
        args = [GOST_BINARY, "-C", cfg.name, "-D"]
        log_path = cfg.name.replace(".yaml", ".log")
        self._log_path = log_path
        log_fh = open(log_path, "w")
        self._log_fh = log_fh
        self.process = subprocess.Popen(
            args,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
        time.sleep(wait_seconds)
        if self.process.poll() is not None:
            rc = self.process.returncode
            self.process = None
            try:
                log_fh.flush(); log_fh.close()
            except OSError:
                pass
            tail = ""
            try:
                with open(log_path, "r", errors="replace") as f:
                    tail = f.read()[-800:].strip()
            except OSError:
                pass
            print(f"[gost] --- stderr/stdout tail ({log_path}) ---")
            print(tail or "(empty)")
            print(f"[gost] --- end tail ---")
            raise RuntimeError(f"gost exited early with code {rc} — see {log_path}")
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
        if getattr(self, "_log_fh", None):
            try:
                self._log_fh.flush(); self._log_fh.close()
            except OSError:
                pass
            self._log_fh = None
        if getattr(self, "_config_path", None):
            try:
                os.unlink(self._config_path)
            except OSError:
                pass
            self._config_path = None
        if getattr(self, "_log_path", None):
            try:
                os.unlink(self._log_path)
            except OSError:
                pass
            self._log_path = None

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
