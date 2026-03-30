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


def build_proxy_username(proxy_config: Dict[str, Any]) -> str:
    """
    Build a residential proxy username with session parameters.

    Credentials (host, base_user, password) come from env vars.
    Location (country, zip) comes from client's proxy config.

    Example output:
      user-spmtfc6iiw-session-a1b2c3d4-sessionduration-30-country-us-zip-94117
    """
    base_user = PROXY_BASE_USER
    session_id = generate_session_id()
    duration = proxy_config.get("session_duration", 30)
    country = proxy_config.get("country", "us")
    zip_code = proxy_config.get("zip", "")

    parts = [
        base_user,
        f"session-{session_id}",
        f"sessionduration-{duration}",
        f"country-{country}",
    ]
    if zip_code:
        parts.append(f"zip-{zip_code}")

    return "-".join(parts)


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

    username = build_proxy_username(proxy_config)

    payload = {
        "deviceId": serial,
        "url": PROXY_HOST,
        "port": PROXY_PORT,
        "username": username,
        "password": PROXY_PASSWORD,
    }

    print(f"  [Proxy] Connecting {serial[:20]}... → {PROXY_HOST}:{PROXY_PORT}")
    print(f"  [Proxy] Username: {username}")

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
        else:
            error = data.get("error", {})
            print(f"  [Proxy] Failed: {status} — {error.get('errorMessage', '')}")

        return {
            "status": status,
            "username": username,
            "proxy_host": PROXY_HOST,
            "proxy_port": PROXY_PORT,
            "response": data,
        }

    except requests.exceptions.ConnectionError:
        print(f"  [Proxy] ERROR — Cannot reach Device Manager at {DEVICE_MANAGER_URL}")
        return {"status": "ERROR", "username": username, "error": "Device Manager unreachable"}
    except Exception as e:
        print(f"  [Proxy] ERROR — {e}")
        return {"status": "ERROR", "username": username, "error": str(e)}


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


def setup_device(serial: str, proxy_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Full device setup for a session: proxy + location + timezone.
    Returns combined result with proxy, location, timezone status.
    """
    result = {}

    # 1. Connect proxy
    proxy_info = connect_proxy(serial, proxy_config)
    result["proxy"] = proxy_info

    # 2. Set mock location (if configured)
    lat = proxy_config.get("latitude")
    lng = proxy_config.get("longitude")
    if lat is not None and lng is not None:
        result["location"] = set_location(serial, lat, lng)

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
