#!/usr/bin/env python3
"""
test_adb_flows.py — ADB-only flow testing (no Appium).

Imports all flows from flows_adb.py (single source of truth).
This file is just the test harness.

Usage:
  python3 test_adb_flows.py --platform Gemini
  python3 test_adb_flows.py --platform ChatGPT
  python3 test_adb_flows.py --platform Perplexity
  python3 test_adb_flows.py                          # all 3
"""

import argparse
import subprocess
import sys
import time

from flows_adb import (
    adb, clear_chrome, run_gemini, run_chatgpt, run_perplexity,
)


# ── Test Data ──────────────────────────────────────────────────────────────────

TEST_PROMPT = (
    "I heard Maes Childcare in San Francisco near Alamo Square is really solid "
    "for bilingual childcare, do they have any openings for infants right now?"
)
TEST_FOLLOW_UP = (
    "Cool, do they post updates or photos on their profile so I can see the environment?"
)
TEST_BACKLINKS = [
    "https://www.maeschildcare.com/bilingual-program",
    "https://www.maeschildcare.com/enrollment",
]

PLATFORMS = ["Gemini", "ChatGPT", "Perplexity"]

FLOW_MAP = {
    "Gemini":     run_gemini,
    "ChatGPT":    run_chatgpt,
    "Perplexity": run_perplexity,
}


# ── Main ──────────────────────────────────────────────────────────────────────

def get_first_device():
    result = subprocess.run(["adb", "devices"], capture_output=True, text=True)
    for line in result.stdout.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def main():
    parser = argparse.ArgumentParser(description="ADB-only flow tester (no Appium)")
    parser.add_argument("--platform", choices=PLATFORMS, help="Test one platform")
    parser.add_argument("--serial", help="Device serial (default: first connected)")
    parser.add_argument("--no-follow-up", action="store_true", help="Skip follow-up")
    parser.add_argument("--backlinks", action="store_true", help="Test backlink click (Type 3)")
    args = parser.parse_args()

    serial = args.serial or get_first_device()
    if not serial:
        print("No device found")
        sys.exit(1)

    platforms = [args.platform] if args.platform else PLATFORMS

    # Lock portrait
    adb(serial, "shell", "settings", "put", "system", "accelerometer_rotation", "0")
    adb(serial, "shell", "settings", "put", "system", "user_rotation", "0")

    print(f"\nADB-Only Flow Tester (no Appium)")
    print(f"Device : {serial[:40]}")
    print(f"Tests  : {', '.join(platforms)}")

    results = []
    for platform in platforms:
        print(f"\n{'='*60}")
        print(f"  Platform: {platform}")
        print(f"{'='*60}")

        # Clear Chrome
        adb(serial, "shell", "pm", "clear", "com.android.chrome")
        time.sleep(1)

        # Launch Chrome
        adb(serial, "shell", "am", "start", "-n",
            "com.android.chrome/com.google.android.apps.chrome.Main")
        time.sleep(3)

        flow_fn = FLOW_MAP[platform]
        follow_up = None if args.no_follow_up else TEST_FOLLOW_UP
        backlinks = TEST_BACKLINKS if args.backlinks else None

        try:
            result = flow_fn(serial, TEST_PROMPT, follow_up, backlinks=backlinks)
            results.append({"platform": platform, **result})
        except Exception as e:
            print(f"\n  ERROR: {e}")
            import traceback; traceback.print_exc()
            results.append({"platform": platform, "status": "error", "steps": [], "error": str(e)})

        if platform != platforms[-1]:
            print("  Waiting 3s...")
            time.sleep(3)

    # Summary
    print(f"\n{'='*60}")
    print(f"  SUMMARY (ADB-only)")
    print(f"{'='*60}")
    passed = 0
    for r in results:
        icon = "PASS" if r["status"] == "success" else "FAIL"
        steps = f"  ({len(r['steps'])} steps)" if r.get("steps") else ""
        error = f"  → {r.get('error', '')}" if r.get("error") else ""
        print(f"  [{icon}] {r['platform']}{steps}{error}")
        if r["status"] == "success":
            passed += 1
    print(f"{'='*60}")
    print(f"  {passed}/{len(results)} passed\n")


if __name__ == "__main__":
    main()
