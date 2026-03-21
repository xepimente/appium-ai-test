"""
AEO Appium Session Runner — v1.0
----------------------------------
Manages per-device Appium sessions with TRUE parallel threading.
Each device gets its own Appium server (port read from session data or active_devices.json)
and its own driver instance. No shared state between device threads.

Ports are NOT hardcoded — they come from active_devices.json via the session dict.
"""

import subprocess
import threading
import time
import traceback
from typing import Callable, Dict, List, Optional, Any

from appium import webdriver
from appium.options.android.uiautomator2.base import UiAutomator2Options

from flows import clear_chrome, run_flow


# ── Per-device locks (prevents ADB collisions on the same device) ──────────────
# Created dynamically from whatever is in the active pool.
# Using a defaultdict-style approach so any device_id gets a lock on first use.

_locks_mutex = threading.Lock()
_device_locks: Dict[str, threading.Lock] = {}


def _get_lock(device_id: str) -> threading.Lock:
    with _locks_mutex:
        if device_id not in _device_locks:
            _device_locks[device_id] = threading.Lock()
        return _device_locks[device_id]


# ── Session Runner ─────────────────────────────────────────────────────────────

def run_session(serial: str, full_serial: str, port: int,
                platform: str, prompt: str, follow_up: Optional[str],
                device_id: str) -> Dict[str, Any]:
    """
    Run one AEO session on a specific device.

    Args:
        serial:      Short device serial
        full_serial: Full ADB transport name
        port:        Appium server port for this device (from active_devices.json)
        platform:    "Gemini", "ChatGPT", or "Perplexity"
        prompt:      Seeding prompt text
        follow_up:   Optional follow-up message
        device_id:   e.g. "device-001"

    Returns:
        {"success": True/False, "device_id": ..., "output": ..., "steps": [...]}
    """
    lock   = _get_lock(device_id)
    driver = None
    lock.acquire()

    try:
        print(f"[{device_id}] Clearing Chrome on {full_serial}...")
        clear_chrome(full_serial)
        time.sleep(1)

        # Lock portrait BEFORE Appium connects
        subprocess.run(
            ["adb", "-s", full_serial, "shell", "settings", "put", "system", "accelerometer_rotation", "0"],
            capture_output=True, timeout=5,
        )
        subprocess.run(
            ["adb", "-s", full_serial, "shell", "settings", "put", "system", "user_rotation", "0"],
            capture_output=True, timeout=5,
        )

        options = UiAutomator2Options()
        options.platform_name       = "Android"
        options.device_name         = serial
        options.udid                = full_serial
        options.automation_name     = "UiAutomator2"
        options.no_reset            = False
        options.new_command_timeout = 300
        options.orientation         = "PORTRAIT"
        options.app_package         = "com.android.chrome"
        options.app_activity        = "com.google.android.apps.chrome.Main"
        options.set_capability("appium:chromeOptions", {"args": []})
        options.set_capability("appium:chromedriverAutodownload", True)

        appium_url = f"http://localhost:{port}/wd/hub"
        print(f"[{device_id}] Connecting to Appium at {appium_url} ({platform})...")

        driver = webdriver.Remote(appium_url, options=options)
        driver.implicitly_wait(5)

        result  = run_flow(platform, driver, full_serial, prompt, follow_up)
        success = result.get("status") == "success"
        output  = f"steps={result.get('steps', [])} error={result.get('error', '')}"

        print(f"[{device_id}] {'SUCCESS' if success else 'FAILED'} — {output}")
        return {
            "success":   success,
            "device_id": device_id,
            "output":    output,
            "steps":     result.get("steps", []),
        }

    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(f"[{device_id}] ERROR — {err}")
        traceback.print_exc()
        return {
            "success":   False,
            "device_id": device_id,
            "output":    err,
            "steps":     [],
        }

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        lock.release()
        print(f"[{device_id}] Session done, lock released.")


# ── Parallel Runner ────────────────────────────────────────────────────────────

def run_parallel(sessions: List[Dict], on_complete: Optional[Callable] = None) -> List[Dict]:
    """
    Run all sessions simultaneously — one thread per device.

    Each session dict must contain:
      device_id, serial, full_serial, port, platform, prompt, follow_up

    on_complete(sess, result) is called immediately after each session finishes.
    Returns list of result dicts.
    """
    results      = []
    results_lock = threading.Lock()
    threads      = []

    def worker(sess):
        port = sess.get("port")
        if not port:
            result = {
                "success":   False,
                "device_id": sess["device_id"],
                "output":    f"No Appium port in session for {sess['device_id']}",
                "steps":     [],
            }
        else:
            result = run_session(
                serial      = sess["serial"],
                full_serial = sess.get("full_serial", sess["serial"]),
                port        = port,
                platform    = sess["platform"],
                prompt      = sess["prompt"],
                follow_up   = sess.get("follow_up"),
                device_id   = sess["device_id"],
            )

        with results_lock:
            results.append(result)

        if on_complete:
            try:
                on_complete(sess, result)
            except Exception as e:
                print(f"[{sess['device_id']}] on_complete error: {e}")

    for sess in sessions:
        t = threading.Thread(target=worker, args=(sess,), daemon=True)
        t.start()
        threads.append(t)
        print(f"Thread started for {sess['device_id']} (port {sess.get('port')})")

    for t in threads:
        t.join()

    return results
