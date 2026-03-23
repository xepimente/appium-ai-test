#!/usr/bin/env python3
"""
setup_devices.py — Discover connected devices, check health, assign for AEO Appium sessions.

Devices are discovered dynamically from 'adb devices' — no hardcoded serials.
Each passing device is assigned a label (device-001, device-002, ...) and a
sequential Appium port starting at 4723.

Checks:
  1. Device is online (in adb devices)
  2. USB debugging (Security Settings) — input injection enabled
  3. Appium UiAutomator2 driver can connect (requires Appium server running)

Usage:
  python setup_devices.py           # discover + check status + Appium test
  python setup_devices.py --check   # discover + check status only (no Appium test)
  python setup_devices.py --assign  # full health check → write active_devices.json
                                    # (only fully passing devices get assigned)
"""

import json
import os
import subprocess
import sys
import concurrent.futures

from appium import webdriver
from appium.options.android.uiautomator2.base import UiAutomator2Options


BASE_DIR            = os.path.dirname(os.path.abspath(__file__))
ACTIVE_DEVICES_FILE = os.path.join(BASE_DIR, "active_devices.json")

BASE_APPIUM_PORT = 4723


def get_connected_devices():
    """
    Discover all connected ADB devices.
    Returns list of full transport names (ready to use with adb -s).
    """
    try:
        result = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=10
        )
    except Exception as e:
        print(f"  adb devices failed: {e}")
        return []

    devices = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices


def get_short_serial(full_transport):
    """
    Extract a stable short serial from a full ADB transport name.
    For wireless ADB: adb-<SERIAL>-<hash>._adb-tls-connect._tcp → <SERIAL>
    For USB / IP:port: use as-is.
    """
    if full_transport.startswith("adb-") and "._adb-tls-connect" in full_transport:
        inner = full_transport[4:]
        inner = inner.split("._adb-tls-connect")[0]
        return inner.rsplit("-", 1)[0]
    return full_transport


def check_inject(full_serial):
    """Return True if device accepts input injection (USB debugging Security Settings on)."""
    try:
        r = subprocess.run(
            ["adb", "-s", full_serial, "shell", "input", "tap", "0", "0"],
            capture_output=True, text=True, timeout=5,
        )
        return "INJECT_EVENTS" not in r.stderr and "INJECT_EVENTS" not in r.stdout
    except Exception:
        return False


def check_appium_server_up(port):
    """Quick HTTP check — is the Appium server on this port responding?"""
    try:
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             f"http://localhost:{port}/wd/hub/status"],
            capture_output=True, text=True, timeout=3,
        )
        return result.stdout.strip() == "200"
    except Exception:
        return False


def check_appium_connect(full_serial, port):
    """
    Try to connect via Appium UiAutomator2 on the given port.
    First checks if the server is even running (avoids hanging on dead ports).
    Returns True if successful, False otherwise.
    """
    # Quick check: is the Appium server even up?
    if not check_appium_server_up(port):
        return False

    try:
        options = UiAutomator2Options()
        options.platform_name       = "Android"
        options.device_name         = full_serial
        options.udid                = full_serial
        options.automation_name     = "UiAutomator2"
        options.no_reset            = True
        options.new_command_timeout = 30

        driver = webdriver.Remote(
            f"http://localhost:{port}/wd/hub",
            options=options,
        )
        driver.quit()
        return True
    except Exception:
        return False


def get_device_model(full_serial):
    """Get device brand and model."""
    try:
        brand = subprocess.run(
            ["adb", "-s", full_serial, "shell", "getprop", "ro.product.brand"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        model = subprocess.run(
            ["adb", "-s", full_serial, "shell", "getprop", "ro.product.model"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return brand, model
    except Exception:
        return "", ""


# Devices that can't run Appium UiAutomator2 — use ADB-only flows
ADB_ONLY_BRANDS = {"infinix", "tecno"}


def check_one_device(args):
    """Check one device. Returns result dict."""
    label, full_serial, short_serial, port, check_only, assign_mode = args

    brand, model = get_device_model(full_serial)
    use_adb = brand.lower() in ADB_ONLY_BRANDS

    result = {
        "label":        label,
        "serial":       short_serial,
        "full_serial":  full_serial,
        "port":         port,
        "brand":        brand,
        "model":        model,
        "use_adb":      use_adb,
        "inject_ok":    False,
        "appium_ok":    None,
        "status":       "unknown",
    }

    # Check 1: USB debugging (Security Settings) — input injection
    inject_ok           = check_inject(full_serial)
    result["inject_ok"] = inject_ok

    # Check 2: Appium connection test (skipped for ADB-only devices and --check mode)
    if use_adb:
        # ADB-only devices skip Appium test — just need inject_ok
        appium_ok = None
        result["appium_ok"] = None
    elif not check_only:
        appium_ok           = check_appium_connect(full_serial, port)
        result["appium_ok"] = appium_ok
    else:
        appium_ok = None

    mode_label = "ADB-only" if use_adb else "Appium"

    if assign_mode:
        issues = []
        if not inject_ok:
            issues.append("USB debugging (Security Settings) OFF")
        if appium_ok is False and not use_adb:
            issues.append(f"Appium FAILED on port {port}")

        if not issues:
            print(f"  OK   {label} ({short_serial[:24]}) port:{port} [{mode_label}] {brand} {model} → assigned")
            result["status"] = "ok"
        else:
            print(f"  FAIL {label} ({short_serial[:24]}) port:{port} [{mode_label}] → {', '.join(issues)}")
            result["status"] = "failed"
        return result

    inject_note = "OK" if inject_ok else "NEEDS FIX"
    appium_note = "OK" if appium_ok else ("ADB-only" if use_adb else ("SKIPPED" if appium_ok is None else "FAILED"))
    ok          = inject_ok and (appium_ok is not False)

    print(f"  [{'OK' if ok else 'ISSUE'}] {label} ({short_serial[:24]}) port:{port} [{mode_label}] | inject:{inject_note} | {appium_note}")
    result["status"] = "ok" if ok else "failed"
    return result


def main(check_only=False, assign_mode=False):
    mode_label = (
        "Assign Healthy Devices" if assign_mode
        else ("Check Only" if check_only else "Check + Appium Test")
    )
    print()
    print("=" * 65)
    print(f"  AEO Appium Device Setup — {mode_label}")
    print("=" * 65)

    connected = get_connected_devices()
    if not connected:
        print("\n  No devices found. Check: adb devices")
        print()
        return

    print(f"\n  Found {len(connected)} connected device(s):\n")

    tasks = []
    for idx, full_serial in enumerate(sorted(connected)):
        label        = f"device-{idx + 1:03d}"
        short_serial = get_short_serial(full_serial)
        port         = BASE_APPIUM_PORT + idx
        tasks.append((label, full_serial, short_serial, port, check_only, assign_mode))

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = [pool.submit(check_one_device, t) for t in tasks]
        for f in concurrent.futures.as_completed(futures):
            try:
                results.append(f.result())
            except Exception as e:
                print(f"  Check error: {e}")

    # Sort by label for consistent display
    results.sort(key=lambda r: r["label"])

    ok_count     = sum(1 for r in results if r["status"] == "ok")
    failed_count = sum(1 for r in results if r["status"] != "ok")

    print()
    print("=" * 65)
    print(f"  Results: {ok_count} ready, {failed_count} need attention")
    print("=" * 65)

    if assign_mode:
        active = {
            r["label"]: {
                "serial":  r["serial"],
                "port":    r["port"],
                "use_adb": r.get("use_adb", False),
                "brand":   r.get("brand", ""),
                "model":   r.get("model", ""),
            }
            for r in results
            if r["status"] == "ok"
        }

        with open(ACTIVE_DEVICES_FILE, "w") as f:
            json.dump(active, f, indent=2)

        print()
        print(f"  {len(active)} device(s) assigned → active_devices.json")
        for label, info in active.items():
            print(f"     • {label}  serial:{info['serial'][:24]}  port:{info['port']}")

        excluded = [r["label"] for r in results if r["status"] != "ok"]
        if excluded:
            print(f"\n  Excluded ({len(excluded)}): {', '.join(excluded)}")
            print("  Fix issues above, then re-run: python setup_devices.py --assign")

        print()
        print("  Next steps:")
        print("    bash start_appium.sh          # start Appium servers")
        print("    python main.py                # run daily sessions")
        print("    python server.py              # start API")

    elif not check_only and failed_count > 0:
        print()
        print("  Appium failures — make sure servers are running:")
        print("    bash start_appium.sh")
        print()
        print("  USB debugging fix:")
        print("    Settings → Developer Options → 'USB debugging (Security Settings)'")
        print("    Reboot the device after enabling.")

    print()


if __name__ == "__main__":
    assign_mode = "--assign" in sys.argv
    check_only  = "--check" in sys.argv and not assign_mode
    main(check_only=check_only, assign_mode=assign_mode)
