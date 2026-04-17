"""
Main task runner — thin wrapper around existing audit.py, flows_adb.py, and flows.py.
Does NOT rewrite any flows. Imports and calls the working code directly.

Two session modes:
  - use_adb=True  → ADB-only (flows_adb.run_flow_adb) — for Infinix, TECNO
  - use_adb=False → Appium   (flows.run_flow)          — for all other brands
"""

import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

# Ensure the project root is on the path so we can import audit, flows_adb, etc.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from audit import (
    audit_gemini_adb,
    audit_chatgpt_adb,
    audit_perplexity_adb,
    build_audit_prompt,
    extract_ranking,
    format_response_text,
)
from flows_adb import (
    clear_chrome as clear_chrome_adb,
    keep_screen_on,
    run_flow_adb,
    adb,
)
from flows import (
    clear_chrome as clear_chrome_appium,
    run_flow as run_flow_appium,
)
from screenshot import extract_response_text

from .result import PlatformResult, TaskResult

# Brands that must use ADB-only (Appium crashes on these)
ADB_ONLY_BRANDS = {"infinix", "tecno"}

BASE_APPIUM_PORT = 4723


AUDIT_FLOW_MAP = {
    "gemini": audit_gemini_adb,
    "chatgpt": audit_chatgpt_adb,
    "perplexity": audit_perplexity_adb,
}


def _is_device_reachable(serial: str) -> bool:
    """Check if device is reachable via ADB."""
    try:
        import subprocess
        result = subprocess.run(
            ["adb", "-s", serial, "shell", "echo", "ok"],
            capture_output=True, text=True, timeout=5,
        )
        return "ok" in result.stdout
    except Exception:
        return False


def _run_platform_audit(serial: str, platform: str, prompt: str,
                        client: Dict, keyword: Dict,
                        cdp_port: int, is_first: bool) -> PlatformResult:
    """Run audit on one platform using existing audit.py functions."""
    platform_lower = platform.lower()
    platform_start = time.time()

    audit_fn = AUDIT_FLOW_MAP.get(platform_lower)
    if not audit_fn:
        return PlatformResult(
            platform=platform_lower, status="error",
            error=f"Unknown platform: {platform}",
            duration_s=round(time.time() - platform_start, 1),
        )

    try:
        # Build client dict in the format audit.py expects
        audit_client = {
            "id": client.get("id", 0),
            "biz_name": client.get("name", ""),
            "biz_url": client.get("url", ""),
            "keyword": keyword.get("text", ""),
            "city": keyword.get("city", ""),
            "state": keyword.get("state", ""),
        }

        ss_path, text_path, timestamp = audit_fn(
            serial, audit_client, keyword.get("text", ""),
            prompt, cdp_port=cdp_port, is_first=is_first,
        )

        # Read the response text that was saved
        response_text = ""
        if os.path.exists(text_path):
            with open(text_path) as f:
                response_text = f.read()

        # Extract ranking from the raw text (re-extract from CDP for better accuracy)
        raw_text = extract_response_text(
            serial, platform_lower.capitalize(), local_port=cdp_port,
        )
        if not raw_text:
            raw_text = response_text

        formatted_text = format_response_text(raw_text, platform_lower)
        ranking_data = extract_ranking(
            raw_text,
            client.get("name", ""),
            client.get("url", ""),
        )

        # Append ranking info to text file
        with open(text_path, "a") as f:
            f.write(f"\n\n--- AEO Ranking ---\n")
            f.write(f"Business: {client.get('name', '')}\n")
            f.write(f"Keyword: {keyword.get('text', '')}\n")
            f.write(f"Platform: {platform_lower.capitalize()}\n")
            f.write(f"Position: {ranking_data['position'] or 'Not ranked'}\n")
            f.write(f"Mentioned: {'Yes' if ranking_data['mentioned'] else 'No'}\n")
            f.write(f"Context: {ranking_data['context']}\n")

        return PlatformResult(
            platform=platform_lower,
            status="success",
            steps=["audit_complete"],
            duration_s=round(time.time() - platform_start, 1),
            response_text=formatted_text,
            screenshot_path=ss_path,
            ranking=ranking_data,
        )

    except Exception as e:
        return PlatformResult(
            platform=platform_lower, status="error",
            error=f"{type(e).__name__}: {e}",
            duration_s=round(time.time() - platform_start, 1),
        )


def _run_platform_session_adb(serial: str, platform: str, prompt: str,
                              follow_up: str, backlinks: List[str],
                              is_first: bool) -> PlatformResult:
    """
    Run session via ADB-only (for Infinix, TECNO).
    Matches session_runner.py ADB path.
    """
    platform_lower = platform.lower()
    platform_cap = platform_lower.capitalize()
    if platform_lower == "chatgpt":
        platform_cap = "ChatGPT"
    platform_start = time.time()

    try:
        if is_first:
            clear_chrome_adb(serial)
            time.sleep(1)
            subprocess.run(
                ["adb", "-s", serial, "shell", "am", "start", "-n",
                 "com.android.chrome/com.google.android.apps.chrome.Main"],
                capture_output=True, timeout=10,
            )
            time.sleep(3)

        result = run_flow_adb(platform_cap, serial, prompt, follow_up,
                              backlinks=backlinks)

        success = result.get("status") == "success"
        return PlatformResult(
            platform=platform_lower,
            status="success" if success else "error",
            steps=result.get("steps", []),
            duration_s=round(time.time() - platform_start, 1),
            error=result.get("error") if not success else None,
        )

    except Exception as e:
        return PlatformResult(
            platform=platform_lower, status="error",
            error=f"{type(e).__name__}: {e}",
            duration_s=round(time.time() - platform_start, 1),
        )


def _run_platform_session_appium(serial: str, full_serial: str, platform: str,
                                 prompt: str, follow_up: str, backlinks: List[str],
                                 is_first: bool, port: int = BASE_APPIUM_PORT) -> PlatformResult:
    """
    Run session via Appium (for Samsung, OPPO, Redmi, Realme, Vivo, etc.).
    Matches session_runner.py Appium path.

    serial: short serial for Appium device_name (e.g., "c0897ffc")
    full_serial: full ADB transport for ADB commands (e.g., "adb-c0897ffc-...")
    """
    from appium import webdriver
    from appium.options.android.uiautomator2.base import UiAutomator2Options

    platform_lower = platform.lower()
    platform_cap = platform_lower.capitalize()
    if platform_lower == "chatgpt":
        platform_cap = "ChatGPT"
    platform_start = time.time()
    driver = None

    try:
        if is_first:
            clear_chrome_appium(full_serial)
            time.sleep(1)
            # Lock portrait
            subprocess.run(
                ["adb", "-s", full_serial, "shell", "settings", "put", "system",
                 "accelerometer_rotation", "0"],
                capture_output=True, timeout=5,
            )
            subprocess.run(
                ["adb", "-s", full_serial, "shell", "settings", "put", "system",
                 "user_rotation", "0"],
                capture_output=True, timeout=5,
            )

        options = UiAutomator2Options()
        options.platform_name = "Android"
        options.device_name = serial
        # Skip udid if transport has (2) — Appium can't parse spaces/parens
        if " " not in full_serial and "(" not in full_serial:
            options.udid = full_serial
        options.automation_name = "UiAutomator2"
        options.no_reset = not is_first
        options.new_command_timeout = 300
        options.orientation = "PORTRAIT"
        options.app_package = "com.android.chrome"
        options.app_activity = "com.google.android.apps.chrome.Main"
        options.set_capability("appium:chromeOptions", {"args": []})
        options.set_capability("appium:chromedriverAutodownload", True)

        appium_url = f"http://localhost:{port}/wd/hub"
        print(f"  Connecting to Appium at {appium_url} ({platform_cap})...")
        driver = webdriver.Remote(appium_url, options=options)
        driver.implicitly_wait(5)

        result = run_flow_appium(platform_cap, driver, full_serial, prompt,
                                follow_up, backlinks=backlinks)

        success = result.get("status") == "success"
        return PlatformResult(
            platform=platform_lower,
            status="success" if success else "error",
            steps=result.get("steps", []),
            duration_s=round(time.time() - platform_start, 1),
            error=result.get("error") if not success else None,
        )

    except Exception as e:
        return PlatformResult(
            platform=platform_lower, status="error",
            error=f"{type(e).__name__}: {e}",
            duration_s=round(time.time() - platform_start, 1),
        )
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def _detect_brand(serial: str) -> str:
    """Get the device brand via ADB."""
    try:
        result = subprocess.run(
            ["adb", "-s", serial, "shell", "getprop", "ro.product.brand"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip().lower()
    except Exception:
        return ""


def execute_task(payload: Dict[str, Any]) -> TaskResult:
    """
    Execute a phone automation task.

    payload keys:
      - type: "audit" | "session"
      - device_serial: str
      - platforms: list[str]
      - client: { name, url, id? }
      - keyword: { text, city, state, id? }
      - prompt: str (session: LLM-generated, audit: auto-built if empty)
      - follow_up: str (session only)
      - backlinks: list[str] (session only)
      - use_adb: bool (optional, auto-detected from brand if not set)
      - port: int (Appium port, default 4723)
    """
    task_start = time.time()
    task_type = payload.get("type", "session")
    device_serial = payload.get("device_serial", "")
    platforms = payload.get("platforms", ["gemini", "chatgpt", "perplexity"])
    client = payload.get("client", {})
    keyword = payload.get("keyword", {})
    cdp_port = 9222

    # Validate device
    if not _is_device_reachable(device_serial):
        return TaskResult(
            status="failed", task_type=task_type, device_serial=device_serial,
            error="device_unreachable",
            duration_seconds=round(time.time() - task_start, 1),
        )

    # Short serial for Appium (e.g., "c0897ffc")
    short_serial = payload.get("serial", "")
    if not short_serial:
        # Derive from full transport: "adb-c0897ffc-XXX._adb-tls-connect._tcp" → "c0897ffc"
        if device_serial.startswith("adb-") and "_adb-tls-connect" in device_serial:
            inner = device_serial[4:]
            inner = inner.split("._adb-tls-connect")[0]
            short_serial = inner.rsplit("-", 1)[0]
        else:
            short_serial = device_serial

    # Detect ADB-only vs Appium
    use_adb = payload.get("use_adb")
    if use_adb is None:
        brand = _detect_brand(device_serial)
        use_adb = brand in ADB_ONLY_BRANDS
        print(f"  Brand: {brand} → {'ADB-only' if use_adb else 'Appium'}")

    appium_port = payload.get("port", BASE_APPIUM_PORT)

    # Keep screen on
    keep_screen_on(device_serial)

    # Build prompt
    prompt = payload.get("prompt", "")
    if not prompt and task_type == "audit":
        audit_client = {
            "keyword": keyword.get("text", ""),
            "city": keyword.get("city", ""),
            "state": keyword.get("state", ""),
            "biz_name": client.get("name", ""),
            "biz_url": client.get("url", ""),
        }
        prompt = build_audit_prompt(audit_client)

    follow_up = payload.get("follow_up", "")
    backlinks = payload.get("backlinks", [])

    # Run each platform
    platform_results: List[PlatformResult] = []
    for i, platform in enumerate(platforms):
        is_first = (i == 0)
        print(f"\n[{device_serial[:20]}] ── {platform.upper()} ──")

        if task_type == "audit":
            result = _run_platform_audit(
                device_serial, platform, prompt, client, keyword,
                cdp_port, is_first,
            )
        elif use_adb:
            result = _run_platform_session_adb(
                device_serial, platform, prompt, follow_up, backlinks,
                is_first,
            )
        else:
            result = _run_platform_session_appium(
                short_serial, device_serial, platform, prompt,
                follow_up, backlinks, is_first, port=appium_port,
            )

        platform_results.append(result)
        status_str = result.status.upper()
        error_str = f" — {result.error}" if result.error else ""
        print(f"[{device_serial[:20]}] {platform} {status_str} — {result.duration_s}s{error_str}")

        # Wait between platforms
        if platform != platforms[-1]:
            time.sleep(3)

    # Determine overall status
    success_count = sum(1 for r in platform_results if r.status == "success")
    if success_count == len(platforms):
        overall_status = "success"
    elif success_count > 0:
        overall_status = "partial_success"
    else:
        overall_status = "failed"

    return TaskResult(
        status=overall_status,
        task_type=task_type,
        device_serial=device_serial,
        duration_seconds=round(time.time() - task_start, 1),
        results=platform_results,
    )
