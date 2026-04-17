"""
AEO Proxy Manager — v1.0
--------------------------
Integrates with Device Manager REST API for SOCKS5 proxy management.
Generates session-based usernames for IP rotation (residential proxy).

Each session gets a unique session ID → unique IP.
Proxy stays connected across all 3 platforms within the same session.
"""

import os
import random
import re
import string
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

# ── Load .env ─────────────────────────────────────────────────────────────────

_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if key and val and key not in os.environ:
                os.environ[key] = val


# ── Config ────────────────────────────────────────────────────────────────────

DEVICE_MANAGER_URL = os.environ.get("DEVICE_MANAGER_URL", "http://localhost:8080")
PROXY_HOST         = os.environ.get("PROXY_HOST", "us.decodo.com")
PROXY_PORT         = int(os.environ.get("PROXY_PORT", "10001"))
PROXY_BASE_USER    = os.environ.get("PROXY_BASE_USER", "user-spmtfc6iiw")
PROXY_PASSWORD     = os.environ.get("PROXY_PASSWORD", "")


# ── Username Generator ────────────────────────────────────────────────────────

def generate_session_id(length: int = 8) -> str:
    """Generate a random session ID for proxy rotation."""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))


def extract_zip_from_address(address: str) -> str:
    """Extract US zip code (5-digit) from an address string."""
    m = re.search(r'\b(\d{5})(?:-\d{4})?\b', address)
    return m.group(1) if m else ""


def enrich_proxy_config(proxy_config: Dict[str, Any], client: Dict[str, Any]) -> Dict[str, Any]:
    """Add zip code to proxy config from client address if not already set."""
    if not proxy_config:
        return proxy_config
    config = dict(proxy_config)
    if not config.get("zip") and client.get("address"):
        zip_code = extract_zip_from_address(client["address"])
        if zip_code:
            config["zip"] = zip_code
            print(f"  [Proxy] Auto-set zip={zip_code} from address")
    return config


def build_proxy_credentials(proxy_config: Dict[str, Any]) -> tuple:
    """
    Build proxy username and password with session parameters.

    For IPRoyal: session/country/city goes in the PASSWORD suffix.
    For Decodo: session/country/zip goes in the USERNAME.

    Returns (username, password).

    IPRoyal format:
      username: O9nlBJemrn7d3nuv
      password: 9mvU8dH2TdVdzbiH_country-us_session-{id}_lifetime-59m

    Decodo format:
      username: user-spmtfc6iiw-session-{id}-sessionduration-30-country-us-zip-94117
      password: (static from env)
    """
    session_id = generate_session_id()
    country = proxy_config.get("country", "us")
    duration = proxy_config.get("session_duration", 60)
    zip_code = proxy_config.get("zip", "")
    city = proxy_config.get("city_proxy", "")  # IPRoyal city targeting

    # Detect provider by hostname
    if "iproyal" in PROXY_HOST:
        # IPRoyal: rotation params go in password
        # Format: {base_password}_country-us_session-{id}_lifetime-60m
        # NOTE: city/state targeting makes password too long for SocksDroid — use country only
        username = PROXY_BASE_USER
        password_parts = [
            PROXY_PASSWORD,
            f"country-{country}",
            f"session-{session_id}",
            f"lifetime-{duration}m",
        ]
        password = "_".join(password_parts)
        return username, password
    else:
        # Decodo: rotation params go in username
        parts = [
            PROXY_BASE_USER,
            f"session-{session_id}",
            f"sessionduration-{duration}",
            f"country-{country}",
        ]
        if zip_code:
            parts.append(f"zip-{zip_code}")
        return "-".join(parts), PROXY_PASSWORD


# Keep backward compat
def build_proxy_username(proxy_config: Dict[str, Any]) -> str:
    """Legacy — returns username only. Use build_proxy_credentials() instead."""
    username, _ = build_proxy_credentials(proxy_config)
    return username


# ── IP Check ──────────────────────────────────────────────────────────────────

def get_device_ip(serial: str, timeout: int = 20) -> Dict[str, Any]:
    """
    Get the device's current public IP.
    Tries: 1) adb shell curl  2) CDP fetch via Chrome  3) Device Manager API
    Then looks up geolocation via ipinfo.io.
    """
    import subprocess

    ip = None

    # Method 1: adb shell curl (works on devices with curl)
    for url in ["http://ifconfig.me", "http://api.ipify.org", "http://icanhazip.com"]:
        try:
            result = subprocess.run(
                ["adb", "-s", serial, "shell", "curl", "-s", "--connect-timeout", "10", url],
                capture_output=True, text=True, timeout=timeout,
            )
            candidate = result.stdout.strip()
            if candidate and len(candidate) <= 45 and "not found" not in candidate.lower() and "inaccessible" not in candidate.lower():
                ip = candidate
                break
        except Exception:
            continue

    # Method 2: Use CDP to fetch IP through Chrome (works on all devices)
    if not ip:
        try:
            from screenshot import cdp_connect, cdp_disconnect, cdp_eval
            for cdp_port in [9222, 9223, 9224, 9225, 9226]:
                try:
                    ws = cdp_connect(serial, local_port=cdp_port)
                    result = cdp_eval(ws, "fetch('http://api.ipify.org').then(r=>r.text())")
                    cdp_disconnect(serial, ws, local_port=cdp_port)
                    candidate = (result or "").strip()
                    if candidate and len(candidate) <= 45 and "." in candidate:
                        ip = candidate
                        break
                except Exception:
                    continue
        except Exception:
            pass

    if not ip:
        return {"ip": "unknown", "error": "failed to get IP from device"}

    # Look up geolocation
    try:
        resp = requests.get(f"https://ipinfo.io/{ip}/json", timeout=5)
        info = resp.json()
        return {
            "ip": ip,
            "city": info.get("city", ""),
            "region": info.get("region", ""),
            "country": info.get("country", ""),
            "postal": info.get("postal", ""),
            "timezone": info.get("timezone", ""),
            "org": info.get("org", ""),
        }
    except Exception:
        return {"ip": ip}


# ── Preflight verification (real IP + proxy IP via Chrome CDP) ────────────────

_REAL_PUBLIC_IP: Optional[str] = None

def get_real_public_ip(timeout: int = 5) -> str:
    """Return the laptop's real public IP (cached). Used as the 'must not equal'
    baseline when verifying proxy routing on the phone."""
    global _REAL_PUBLIC_IP
    if _REAL_PUBLIC_IP is None:
        for url in ["https://ifconfig.me/ip", "https://api.ipify.org", "https://icanhazip.com"]:
            try:
                r = requests.get(url, timeout=timeout)
                ip = r.text.strip()
                if ip and len(ip) <= 45:
                    _REAL_PUBLIC_IP = ip
                    break
            except Exception:
                continue
        if _REAL_PUBLIC_IP is None:
            _REAL_PUBLIC_IP = ""
    return _REAL_PUBLIC_IP


def verify_proxy_via_chrome(serial: str, cdp_port: int = 9222,
                             timeout: int = 30) -> Dict[str, Any]:
    """Launch Chrome to ifconfig.me/ip on the device and read the IP back via CDP.
    Compare to the laptop's real public IP. Returns dict with ok/ip/reason.
    This is the only reliable way to prove the proxy is actually routing
    (SocksDroid's tun0 can be up while the route table falls back to wlan0)."""
    import json as _json
    import re
    import subprocess
    import urllib.request
    import websocket

    real_ip = get_real_public_ip()

    # Force-stop + launch Chrome to ifconfig.me, then dismiss any FRE (account
    # screen / welcome) that may be blocking the page load, then re-navigate.
    # We intentionally do NOT run `pm clear` here — it would wipe Chrome data
    # every run and force FRE every time. Stale tabs from previous runs are
    # fine because the page picker below filters by URL.
    subprocess.run(["adb", "-s", serial, "shell", "am", "force-stop", "com.android.chrome"],
                   capture_output=True, timeout=10)
    time.sleep(1)
    subprocess.run(["adb", "-s", serial, "shell", "am", "start",
                    "--activity-clear-task", "-a", "android.intent.action.VIEW",
                    "-d", "https://ifconfig.me/ip", "com.android.chrome"],
                   capture_output=True, timeout=10)
    time.sleep(3)

    try:
        from flows_adb import dismiss_chrome_fre
        dismiss_chrome_fre(serial)
        # FRE dismissal may have left Chrome on a blank NTP — re-navigate.
        subprocess.run(["adb", "-s", serial, "shell", "am", "start",
                        "-a", "android.intent.action.VIEW",
                        "-d", "https://ifconfig.me/ip", "com.android.chrome"],
                       capture_output=True, timeout=10)
        time.sleep(2)
    except Exception as e:
        print(f"  [Preflight] FRE dismiss error (continuing): {e}")

    # Forward CDP port once; use raw /json listing so we can pick the
    # ifconfig tab explicitly (cdp_connect in screenshot.py prefers AI-platform
    # URLs and would grab a stale tab).
    subprocess.run(["adb", "-s", serial, "forward", f"tcp:{cdp_port}",
                    "localabstract:chrome_devtools_remote"],
                   capture_output=True, timeout=5)

    start = time.time()
    ip = None
    last_err = ""
    try:
        while time.time() - start < timeout:
            time.sleep(2)
            try:
                resp = urllib.request.urlopen(f"http://localhost:{cdp_port}/json", timeout=5)
                pages = _json.loads(resp.read())
            except Exception as e:
                last_err = f"json list: {e}"
                continue

            target = None
            for p in pages:
                url = p.get("url", "")
                if "ifconfig.me" in url and p.get("type") == "page":
                    target = p
                    break
            if not target:
                last_err = "ifconfig.me tab not found yet"
                continue

            ws_url = target.get("webSocketDebuggerUrl")
            if not ws_url:
                last_err = "no webSocketDebuggerUrl"
                continue

            try:
                ws = websocket.create_connection(ws_url, suppress_origin=True, timeout=5)
                ws.send(_json.dumps({
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": "document.body ? document.body.innerText : ''",
                        "returnByValue": True,
                    },
                }))
                reply = _json.loads(ws.recv())
                ws.close()
                text = (reply.get("result", {}).get("result", {}).get("value", "") or "").strip()
                if text:
                    m = re.search(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", text)
                    if m:
                        ip = m.group(1)
                        break
                else:
                    last_err = "empty body"
            except Exception as e:
                last_err = f"ws eval: {e}"
                continue
    finally:
        subprocess.run(["adb", "-s", serial, "forward", "--remove", f"tcp:{cdp_port}"],
                       capture_output=True, timeout=5)

    if not ip:
        return {"ok": False, "ip": "", "real_ip": real_ip,
                "reason": f"could not read ip from chrome ({last_err})"}

    if real_ip and ip == real_ip:
        return {"ok": False, "ip": ip, "real_ip": real_ip,
                "reason": f"proxy leak — device showing real IP {ip}"}

    return {"ok": True, "ip": ip, "real_ip": real_ip, "reason": "ok"}


# ── Device Manager API ────────────────────────────────────────────────────────

def connect_proxy(serial: str, proxy_config: Dict[str, Any],
                  timeout: int = 30) -> Dict[str, Any]:
    """
    Connect a device to SOCKS5 proxy via Device Manager API.

    Credentials come from env vars (PROXY_HOST, PROXY_PORT, PROXY_BASE_USER, PROXY_PASSWORD).
    Location (country, zip, session_duration) comes from proxy_config (client-specific).

    Args:
        serial: Full ADB device serial
        proxy_config: Dict with country, zip, session_duration (location-specific)
        timeout: HTTP request timeout in seconds

    Returns:
        Dict with status, username used, and any error info.
    """
    if not PROXY_PASSWORD:
        print("  [Proxy] PROXY_PASSWORD not set — skipping proxy connection")
        return {"status": "SKIPPED", "error": "PROXY_PASSWORD env var not set"}

    # Grant VPN permission before connecting (pm clear resets it)
    import subprocess
    try:
        subprocess.run(
            ["adb", "-s", serial, "shell", "appops", "set",
             "net.typeblog.socks", "ACTIVATE_VPN", "allow"],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass

    username, password = build_proxy_credentials(proxy_config)

    payload = {
        "deviceId": serial,
        "url": PROXY_HOST,
        "port": PROXY_PORT,
        "username": username,
        "password": password,
    }

    print(f"  [Proxy] Connecting {serial[:20]}... → {PROXY_HOST}:{PROXY_PORT}")
    print(f"  [Proxy] Credentials: {username} / {password[:30]}...")

    try:
        resp = requests.post(
            f"{DEVICE_MANAGER_URL}/device/proxy/connect",
            json=payload,
            timeout=timeout,
        )
        data = resp.json()
        status = data.get("status", "UNKNOWN")

        if status == "CONNECTED":
            print(f"  [Proxy] Connected successfully")
            # Wait for VPN to stabilize. Skipping get_device_ip here because it
            # hangs on CDP websocket when Chrome isn't open yet; IP check happens
            # later once Chrome is up.
            time.sleep(3)
            ip_info = {}
        else:
            ip_info = {}
            error = data.get("error", {})
            print(f"  [Proxy] Failed: {status} — {error.get('errorMessage', '')}")

        return {
            "status": status,
            "username": username,
            "proxy_host": PROXY_HOST,
            "proxy_port": PROXY_PORT,
            "ip": ip_info.get("ip"),
            "ip_city": ip_info.get("city"),
            "ip_region": ip_info.get("region"),
            "ip_country": ip_info.get("country"),
            "ip_zip": ip_info.get("postal"),
            "response": data,
        }

    except requests.exceptions.ConnectionError:
        print(f"  [Proxy] ERROR — Cannot reach Device Manager at {DEVICE_MANAGER_URL}")
        return {"status": "ERROR", "username": username, "error": "Device Manager unreachable"}
    except Exception as e:
        print(f"  [Proxy] ERROR — {e}")
        return {"status": "ERROR", "username": username, "error": str(e)}


def verify_connection(serial: str, timeout: int = 15) -> bool:
    """
    Verify device has working internet through proxy.
    Curls a lightweight URL and checks for a valid response.
    Returns True if connection works, False otherwise.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["adb", "-s", serial, "shell", "curl", "-s", "--connect-timeout", "10",
             "http://httpbin.org/status/200"],
            capture_output=True, text=True, timeout=timeout,
        )
        # httpbin returns empty body with 200 status — success if no error
        if result.returncode == 0 and "could not resolve" not in result.stderr.lower():
            print(f"  [Proxy] Connection verified — internet working")
            return True
    except Exception:
        pass

    print(f"  [Proxy] Connection verification FAILED — no internet")
    return False


def disconnect_proxy(serial: str, timeout: int = 15) -> Dict[str, Any]:
    """
    Disconnect proxy from device via Device Manager API.

    Args:
        serial: Full ADB device serial
        timeout: HTTP request timeout in seconds

    Returns:
        Dict with status and any error info.
    """
    print(f"  [Proxy] Disconnecting {serial[:20]}...")

    try:
        resp = requests.post(
            f"{DEVICE_MANAGER_URL}/device/proxy/disconnect/{serial}",
            timeout=timeout,
        )
        data = resp.json()
        status = data.get("status", "UNKNOWN")

        if status == "DISCONNECTED":
            print(f"  [Proxy] Disconnected successfully")
        else:
            print(f"  [Proxy] Disconnect status: {status}")

        return {"status": status, "response": data}

    except requests.exceptions.ConnectionError:
        print(f"  [Proxy] ERROR — Cannot reach Device Manager at {DEVICE_MANAGER_URL}")
        return {"status": "ERROR", "error": "Device Manager unreachable"}
    except Exception as e:
        print(f"  [Proxy] ERROR — {e}")
        return {"status": "ERROR", "error": str(e)}


def set_location(serial: str, latitude: float, longitude: float,
                 timeout: int = 15) -> Dict[str, Any]:
    """Set mock GPS location on device via Device Manager API."""
    print(f"  [Location] Setting {serial[:20]}... → ({latitude}, {longitude})")

    try:
        resp = requests.post(
            f"{DEVICE_MANAGER_URL}/device/location/mock",
            json={
                "deviceId": serial,
                "latitude": latitude,
                "longitude": longitude,
            },
            timeout=timeout,
        )
        data = resp.json()
        print(f"  [Location] Set successfully")
        return {"status": "OK", "response": data}

    except requests.exceptions.ConnectionError:
        print(f"  [Location] ERROR — Cannot reach Device Manager")
        return {"status": "ERROR", "error": "Device Manager unreachable"}
    except Exception as e:
        print(f"  [Location] ERROR — {e}")
        return {"status": "ERROR", "error": str(e)}


def set_timezone(serial: str, timezone: str,
                 timeout: int = 15) -> Dict[str, Any]:
    """Set device timezone via Device Manager API."""
    print(f"  [Timezone] Setting {serial[:20]}... → {timezone}")

    try:
        resp = requests.post(
            f"{DEVICE_MANAGER_URL}/device/timezone",
            json={
                "deviceId": serial,
                "timezone": timezone,
            },
            timeout=timeout,
        )
        data = resp.json()
        print(f"  [Timezone] Set successfully")
        return {"status": "OK", "response": data}

    except requests.exceptions.ConnectionError:
        print(f"  [Timezone] ERROR — Cannot reach Device Manager")
        return {"status": "ERROR", "error": "Device Manager unreachable"}
    except Exception as e:
        print(f"  [Timezone] ERROR — {e}")
        return {"status": "ERROR", "error": str(e)}


def randomize_location(latitude: float, longitude: float,
                       radius_miles: float = 5.0) -> tuple:
    """
    Randomize lat/lng within a radius (in miles) of the center point.
    Returns (new_lat, new_lng).

    1 degree latitude ≈ 69 miles
    1 degree longitude ≈ 69 * cos(lat) miles
    """
    import math

    radius_deg_lat = radius_miles / 69.0
    radius_deg_lng = radius_miles / (69.0 * math.cos(math.radians(latitude)))

    # Random point within circle
    angle = random.uniform(0, 2 * math.pi)
    distance = random.uniform(0, 1) ** 0.5  # sqrt for uniform distribution in circle
    offset_lat = distance * radius_deg_lat * math.sin(angle)
    offset_lng = distance * radius_deg_lng * math.cos(angle)

    return round(latitude + offset_lat, 6), round(longitude + offset_lng, 6)


def _wait_for_internet(serial: str, max_wait: int = 15) -> bool:
    """
    Wait until device has working internet through proxy.
    Checks if tun0 VPN interface is UP (doesn't test actual throughput — that's
    too slow and ICMP doesn't go through SOCKS5 anyway).
    Returns True if VPN tunnel is active, False if not.
    """
    import subprocess

    start = time.time()
    attempt = 0
    while time.time() - start < max_wait:
        attempt += 1
        try:
            result = subprocess.run(
                ["adb", "-s", serial, "shell", "ifconfig", "tun0"],
                capture_output=True, text=True, timeout=5,
            )
            if "UP" in result.stdout and "inet addr" in result.stdout:
                elapsed = int(time.time() - start)
                print(f"  [Proxy] VPN tunnel active — tun0 UP ({elapsed}s, attempt {attempt})")
                return True
        except Exception:
            pass
        time.sleep(2)

    print(f"  [Proxy] VPN tunnel not found after {max_wait}s")
    return False


def setup_device(serial: str, proxy_config: Dict[str, Any],
                  max_retries: int = 2) -> Dict[str, Any]:
    """
    Full device setup for a session: proxy + location + timezone.
    Location is randomized within 5 miles of the base coordinates.
    After connecting proxy, waits for VPN tunnel to be active.
    If connect fails, retries once with new session ID.
    Returns combined result with proxy, location, timezone status.
    """
    result = {}

    proxy_info = None
    for attempt in range(1, max_retries + 1):
        proxy_info = connect_proxy(serial, proxy_config)

        if proxy_info.get("status") != "CONNECTED":
            print(f"  [Proxy] Connect failed (attempt {attempt}/{max_retries})")
            if attempt < max_retries:
                disconnect_proxy(serial)
                time.sleep(3)
            continue

        # Wait for VPN tunnel to come up
        time.sleep(3)
        if _wait_for_internet(serial, max_wait=15):
            break

        # tun0 not up — retry once
        if attempt < max_retries:
            print(f"  [Proxy] VPN tunnel not ready — retrying (attempt {attempt}/{max_retries})...")
            disconnect_proxy(serial)
            time.sleep(3)
        else:
            # API said CONNECTED — trust it and proceed
            print(f"  [Proxy] tun0 not detected but API says CONNECTED — proceeding")

    result["proxy"] = proxy_info

    # 2. Set mock location — randomized within 5 miles
    lat = proxy_config.get("latitude")
    lng = proxy_config.get("longitude")
    if lat is not None and lng is not None:
        rand_lat, rand_lng = randomize_location(lat, lng, radius_miles=5.0)
        print(f"  [Location] Randomized: ({lat}, {lng}) → ({rand_lat}, {rand_lng})")
        result["location"] = set_location(serial, rand_lat, rand_lng)

    # 3. Set timezone (if configured)
    tz = proxy_config.get("timezone")
    if tz:
        result["timezone"] = set_timezone(serial, tz)

    return result


def teardown_device(serial: str) -> Dict[str, Any]:
    """Disconnect proxy after session. Location/timezone can stay."""
    return disconnect_proxy(serial)


def check_device_manager(timeout: int = 5) -> bool:
    """Check if Device Manager API is reachable."""
    try:
        resp = requests.get(
            f"{DEVICE_MANAGER_URL}/device/list",
            timeout=timeout,
        )
        return resp.status_code == 200
    except Exception:
        return False
