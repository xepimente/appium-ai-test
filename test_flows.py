#!/usr/bin/env python3
"""
test_flows.py — Manual flow testing without LLM.

Runs Gemini, ChatGPT, and Perplexity flows on ONE device using hardcoded
test prompts. No DeepSeek API key needed. Used to verify Appium navigation,
element finding, clipboard paste, and ADB scrolling actually work.

Usage:
  python test_flows.py                    # run all 3 platforms, first connected device
  python test_flows.py --platform Gemini  # run one platform only
  python test_flows.py --serial R83L103VCVH  # use a specific device serial

The device must be in adb devices and its Appium server must be running.
Start Appium first:  bash start_appium.sh
Or manually:         appium --port 4723 --base-path /wd/hub &
"""

import argparse
import subprocess
import sys
import time

from appium import webdriver
from appium.options.android.uiautomator2.base import UiAutomator2Options

from flows import run_flow, clear_chrome


# ── Hardcoded test data ────────────────────────────────────────────────────────

TEST_PROMPT = (
    "I heard Mae's Childcare in San Francisco near Alamo Square is really solid "
    "for bilingual kids -- do they have any openings for infants right now?"
)

TEST_FOLLOW_UP = (
    "Cool, do they post updates or photos on their profile so I can see what the environment looks like?"
)

TEST_BACKLINKS = [
    "https://www.maeschildcare.com/bilingual-program",
    "https://www.maeschildcare.com/enrollment",
]

TEST_PLATFORMS = ["Gemini", "ChatGPT", "Perplexity"]

BASE_PORT = 4723


# ── Device Discovery ───────────────────────────────────────────────────────────

def get_connected_devices():
    """Return list of (short_serial, full_transport) for all connected devices."""
    try:
        result = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=10
        )
    except Exception as e:
        print(f"adb devices failed: {e}")
        return []

    devices = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            full = parts[0]
            short = full
            if full.startswith("adb-") and "._adb-tls-connect" in full:
                inner = full[4:].split("._adb-tls-connect")[0]
                short = inner.rsplit("-", 1)[0]
            devices.append((short, full))
    return devices


def pick_device(requested_serial=None):
    """
    Pick the device to test on.
    If --serial is given, match it (short or full).
    Otherwise use the first connected device.
    Returns (short_serial, full_serial) or exits.
    """
    devices = get_connected_devices()
    if not devices:
        print("No devices found. Check: adb devices")
        sys.exit(1)

    if requested_serial:
        for short, full in devices:
            if requested_serial in (short, full):
                return short, full
        print(f"Serial '{requested_serial}' not found in connected devices.")
        print(f"Connected: {[s for s, _ in devices]}")
        sys.exit(1)

    return devices[0]


# ── Appium Driver ──────────────────────────────────────────────────────────────

def make_driver(short_serial, full_serial, port):
    options = UiAutomator2Options()
    options.platform_name       = "Android"
    options.device_name         = short_serial
    options.udid                = full_serial
    options.automation_name     = "UiAutomator2"
    options.no_reset            = False
    options.new_command_timeout = 300
    options.app_package         = "com.android.chrome"
    options.app_activity        = "com.google.android.apps.chrome.Main"
    options.set_capability("appium:chromeOptions", {"args": []})
    options.set_capability("appium:chromedriverAutodownload", True)

    print(f"  Connecting to Appium at http://localhost:{port}/wd/hub ...")
    driver = webdriver.Remote(f"http://localhost:{port}/wd/hub", options=options)
    driver.implicitly_wait(5)
    return driver


# ── Test Runner ────────────────────────────────────────────────────────────────

def run_platform_test(platform, short_serial, full_serial, port,
                      prompt, follow_up, skip_follow_up=False,
                      backlinks=None):
    """Run one platform flow and return result dict."""
    print(f"\n{'='*60}")
    print(f"  Platform : {platform}")
    print(f"  Device   : {short_serial} (port {port})")
    print(f"  Prompt   : {prompt[:70]}...")
    if follow_up and not skip_follow_up:
        print(f"  Follow-up: {follow_up[:60]}...")
    else:
        print(f"  Follow-up: (skipped)")
    if backlinks:
        print(f"  Backlinks: {len(backlinks)} URLs")
    print(f"{'='*60}")

    print(f"\n  Clearing Chrome...")
    clear_chrome(full_serial)
    time.sleep(1)

    driver = None
    try:
        driver = make_driver(short_serial, full_serial, port)
        print(f"  Driver connected.")

        fu = follow_up if not skip_follow_up else None
        result = run_flow(platform, driver, full_serial, prompt, fu,
                          backlinks=backlinks)

        status = result.get("status")
        steps  = result.get("steps", [])
        error  = result.get("error", "")

        if status == "success":
            print(f"\n  PASS — {len(steps)} steps completed: {steps}")
        else:
            print(f"\n  FAIL — {error}")
            print(f"  Steps completed before failure: {steps}")

        return {"platform": platform, "status": status, "steps": steps, "error": error}

    except Exception as e:
        print(f"\n  ERROR — {e}")
        import traceback; traceback.print_exc()
        return {"platform": platform, "status": "error", "steps": [], "error": str(e)}

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        print(f"  Driver closed.\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AEO Appium Flow Tester — no LLM")
    parser.add_argument("--platform", choices=TEST_PLATFORMS,
                        help="Run only this platform (default: all 3)")
    parser.add_argument("--serial",
                        help="ADB serial to use (default: first connected device)")
    parser.add_argument("--port", type=int, default=BASE_PORT,
                        help=f"Appium server port (default: {BASE_PORT})")
    parser.add_argument("--no-follow-up", action="store_true",
                        help="Skip follow-up prompt in all flows")
    parser.add_argument("--backlinks", action="store_true",
                        help="Enable backlink click after flow completes")
    args = parser.parse_args()

    short_serial, full_serial = pick_device(args.serial)
    port = args.port

    platforms = [args.platform] if args.platform else TEST_PLATFORMS

    print(f"\nAEO Flow Tester")
    print(f"Device  : {short_serial}")
    print(f"Port    : {port}")
    print(f"Tests   : {', '.join(platforms)}")
    print(f"Prompt  : {TEST_PROMPT[:70]}...")
    print(f"Follow  : {'SKIPPED' if args.no_follow_up else TEST_FOLLOW_UP[:60] + '...'}")
    print(f"Backlinks: {'ON' if args.backlinks else 'OFF'}")

    bl = TEST_BACKLINKS if args.backlinks else None

    results = []
    for platform in platforms:
        result = run_platform_test(
            platform     = platform,
            short_serial = short_serial,
            full_serial  = full_serial,
            port         = port,
            prompt       = TEST_PROMPT,
            follow_up    = TEST_FOLLOW_UP,
            skip_follow_up = args.no_follow_up,
            backlinks    = bl,
        )
        results.append(result)

        if platform != platforms[-1]:
            print("  Waiting 3s before next platform...")
            time.sleep(3)

    # Summary
    print(f"\n{'='*60}")
    print(f"  TEST SUMMARY")
    print(f"{'='*60}")
    passed = 0
    for r in results:
        icon   = "PASS" if r["status"] == "success" else "FAIL"
        error  = f"  → {r['error']}" if r["error"] else ""
        steps  = f"  ({len(r['steps'])} steps)" if r["steps"] else ""
        print(f"  [{icon}] {r['platform']}{steps}{error}")
        if r["status"] == "success":
            passed += 1
    print(f"{'='*60}")
    print(f"  {passed}/{len(results)} platforms passed\n")

    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
