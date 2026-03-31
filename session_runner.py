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
from flows_adb import run_flow_adb, clear_chrome as clear_chrome_adb
from proxy import setup_device, teardown_device


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

def _run_single_platform(serial: str, full_serial: str, port: int,
                         platform: str, prompt: str, follow_up: Optional[str],
                         device_id: str, use_adb: bool,
                         backlinks: List,
                         is_first: bool = True) -> Dict[str, Any]:
    """
    Run one platform flow on a specific device.
    Internal helper — does NOT manage proxy or locks.

    is_first: if True, clears Chrome and launches fresh.
              if False, Chrome is already open — just navigate to new platform.
    """
    driver = None
    platform_start = time.time()
    try:
        if use_adb:
            print(f"[{device_id}] ADB-only mode — {platform}")
            if is_first:
                clear_chrome_adb(full_serial)
                time.sleep(1)
                subprocess.run(
                    ["adb", "-s", full_serial, "shell", "am", "start", "-n",
                     "com.android.chrome/com.google.android.apps.chrome.Main"],
                    capture_output=True, timeout=10,
                )
                time.sleep(3)

            result = run_flow_adb(platform, full_serial, prompt, follow_up, backlinks=backlinks)

        else:
            if is_first:
                print(f"[{device_id}] Clearing Chrome on {full_serial}...")
                clear_chrome(full_serial)
                time.sleep(1)

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
            options.no_reset            = True if not is_first else False
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

            result = run_flow(platform, driver, full_serial, prompt, follow_up, backlinks=backlinks)

        success = result.get("status") == "success"
        duration = round(time.time() - platform_start, 1)
        error = result.get("error", "")

        print(f"[{device_id}] {platform} {'SUCCESS' if success else 'FAILED'} — {duration}s{' — ' + error if error else ''}")
        return {
            "success":    success,
            "device_id":  device_id,
            "platform":   platform,
            "duration_s": duration,
            "error":      error,
        }

    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        duration = round(time.time() - platform_start, 1)
        print(f"[{device_id}] {platform} ERROR — {err}")
        traceback.print_exc()
        return {
            "success":    False,
            "device_id":  device_id,
            "platform":   platform,
            "duration_s": duration,
            "error":      err,
        }

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def run_session(serial: str, full_serial: str, port: int,
                platform: str, prompt: str, follow_up: Optional[str],
                device_id: str, sess_meta: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Run one AEO session on a specific device.

    If sess_meta contains "platforms" (list), runs all platforms sequentially
    under the same proxy session. Otherwise runs a single platform.

    Proxy is connected before platforms and disconnected after all complete.

    Returns:
        {"success": True/False, "device_id": ..., "output": ..., "steps": [...],
         "proxy": {...}, "platform_results": [...]}
    """
    lock    = _get_lock(device_id)
    meta    = sess_meta if isinstance(sess_meta, dict) else {}
    use_adb   = meta.get("use_adb", False)
    backlinks = meta.get("backlinks", [])
    proxy_config = meta.get("proxy", None)
    platforms = meta.get("platforms", [platform])

    lock.acquire()
    proxy_info = None

    try:
        # ── Setup device: proxy + location + timezone ──
        if proxy_config:
            print(f"[{device_id}] Setting up device (proxy + location + timezone)...")
            device_setup = setup_device(full_serial, proxy_config)
            proxy_info = device_setup.get("proxy", {})
            if proxy_info.get("status") != "CONNECTED":
                print(f"[{device_id}] Proxy connection failed — continuing without proxy")
            else:
                time.sleep(5)  # Let VPN stabilize

        # ── Run all platforms sequentially under same proxy ──
        platform_results = []
        all_steps = []

        for i, plat in enumerate(platforms):
            print(f"\n[{device_id}] ── {plat} ──")
            result = _run_single_platform(
                serial=serial, full_serial=full_serial, port=port,
                platform=plat, prompt=prompt, follow_up=follow_up,
                device_id=device_id, use_adb=use_adb, backlinks=backlinks,
                is_first=(i == 0),
            )
            platform_results.append(result)
            all_steps.extend(result.get("steps", []))

            # Wait between platforms
            if plat != platforms[-1]:
                print(f"[{device_id}] Waiting 3s before next platform...")
                time.sleep(3)

        overall_success = all(r.get("success") for r in platform_results)
        output = f"platforms={len(platforms)} passed={sum(1 for r in platform_results if r.get('success'))}"

        print(f"[{device_id}] {'ALL PASSED' if overall_success else 'SOME FAILED'} — {output}")
        return {
            "success":          overall_success,
            "device_id":        device_id,
            "output":           output,
            "steps":            all_steps,
            "proxy":            proxy_info,
            "platform_results": platform_results,
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
            "proxy":     proxy_info,
        }

    finally:
        # ── Teardown: disconnect proxy ──
        if proxy_config:
            try:
                teardown_device(full_serial)
            except Exception as e:
                print(f"[{device_id}] Teardown error: {e}")

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
        port    = sess.get("port")
        use_adb = sess.get("use_adb", False)

        if not port and not use_adb:
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
                port        = port or 0,
                platform    = sess["platform"],
                prompt      = sess["prompt"],
                follow_up   = sess.get("follow_up"),
                device_id   = sess["device_id"],
                sess_meta   = {
                    "use_adb": use_adb,
                    "backlinks": sess.get("backlinks", []),
                    "proxy": sess.get("proxy"),
                    "platforms": sess.get("platforms", [sess["platform"]]),
                },
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
