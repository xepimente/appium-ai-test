#!/usr/bin/env python3
"""
check_devices.py — Identify which devices need 'USB debugging (Security Settings)' enabled.
Runs all checks in parallel with a 5-second timeout per device.
"""

import subprocess
import concurrent.futures


def get_devices():
    result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
    devices = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices


def get_prop(serial, prop):
    try:
        r = subprocess.run(
            ["adb", "-s", serial, "shell", "getprop", prop],
            capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip()
    except Exception:
        return "unknown"


def check_inject(serial):
    try:
        r = subprocess.run(
            ["adb", "-s", serial, "shell", "input", "tap", "0", "0"],
            capture_output=True, text=True, timeout=5
        )
        return "INJECT_EVENTS" not in r.stderr and "INJECT_EVENTS" not in r.stdout
    except Exception:
        return None  # timeout or connection error


def check_device(args):
    idx, serial = args
    brand = get_prop(serial, "ro.product.brand")
    model = get_prop(serial, "ro.product.model")
    can_inject = check_inject(serial)

    label = f"[{idx:>2}]"
    device_str = f"{brand} {model} | {serial}"

    if can_inject is None:
        return f"  {label} ⚠️  TIMEOUT     | {device_str}"
    elif can_inject:
        return f"  {label} ✅ OK          | {device_str}"
    else:
        return f"  {label} ❌ NEEDS FIX  | {device_str}"


def main():
    print()
    print("Checking USB debugging (Security Settings) on all connected devices...")
    print("=" * 70)

    devices = get_devices()
    if not devices:
        print("No devices found. Check 'adb devices'.")
        return

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(devices)) as pool:
        futures = {pool.submit(check_device, (i + 1, s)): s for i, s in enumerate(devices)}
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    for line in sorted(results):
        print(line)

    needs_fix = sum(1 for r in results if "NEEDS FIX" in r)
    ok = sum(1 for r in results if "✅" in r)

    print()
    print(f"Results: {ok} OK, {needs_fix} need 'USB debugging (Security Settings)' enabled")

    if needs_fix > 0:
        print()
        print("How to fix (on each ❌ device):")
        print("  1. Go to Settings → Developer Options")
        print("  2. Enable 'USB debugging (Security Settings)'  ← the SECOND USB debugging toggle")
        print("  3. Reboot the device")
        print()
        print("Brand-specific paths:")
        print("  Xiaomi/MIUI   : Settings → Additional Settings → Developer Options")
        print("                  → 'USB debugging (Security Settings)'")
        print("  OPPO/Realme   : Settings → Additional Settings → Developer Options")
        print("                  → 'Disable permission monitoring'")
        print("  Infinix/TECNO : Settings → System → Developer Options")
        print("                  → 'USB debugging (Security Settings)'")
        print("  Samsung       : Settings → Developer Options → toggle USB debugging off/on → Reboot")
        print("  Vivo/iQOO     : Settings → Developer Options → 'USB debugging (Security Settings)'")


if __name__ == "__main__":
    main()
