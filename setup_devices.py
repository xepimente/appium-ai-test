#!/usr/bin/env python3
"""
setup_devices.py — Check, install, and assign devices for AEO sessions.

Usage:
  python setup_devices.py           # check status + install Portal where needed
  python setup_devices.py --check   # check status only, no install
  python setup_devices.py --assign  # full health check + write active_devices.json
                                    # (only fully passing devices get assigned)
"""

import asyncio
import json
import os
import subprocess
import sys
from async_adbutils import adb
from droidrun.portal import (
    PORTAL_PACKAGE_NAME,
    setup_portal,
    check_portal_accessibility,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACTIVE_DEVICES_FILE = os.path.join(BASE_DIR, "active_devices.json")

DEVICE_POOL = {
    "device-001": "0B64C27G22100FA1",
    "device-002": "0B64C27G23101E10",
    "device-003": "10HFBBFEBZ000RA",
    "device-004": "147263758M001636",
    "device-005": "1490455613010287",
    "device-006": "323952008165",
    "device-007": "324651961440",
    "device-008": "G6TGT4SCTCYTDMDU",
    "device-009": "NJ65ONAACQVWBAMR",
    "device-010": "NZNBYL5DE6GIK7VS",
    "device-011": "R83L103VCVH",
    "device-012": "R83L112EVWK",
    "device-013": "c0897ffc",
}


def get_transport_map():
    """Map short serial → full adb transport name."""
    try:
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
    except Exception:
        return {}
    transport_map = {}
    for line in result.stdout.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            transport = parts[0]
            if transport.startswith("adb-") and "._adb-tls-connect" in transport:
                inner = transport[4:]
                inner = inner.split("._adb-tls-connect")[0]
                short_serial = inner.rsplit("-", 1)[0]
                transport_map[short_serial] = transport
            else:
                transport_map[transport] = transport
    return transport_map


def check_inject(full_serial):
    """Return True if the device can accept input injection (Security Settings enabled)."""
    try:
        r = subprocess.run(
            ["adb", "-s", full_serial, "shell", "input", "tap", "0", "0"],
            capture_output=True, text=True, timeout=5,
        )
        return "INJECT_EVENTS" not in r.stderr and "INJECT_EVENTS" not in r.stdout
    except Exception:
        return False


async def check_device(device_label, full_serial, install=True, assign_mode=False):
    """
    Check one device. In assign mode, all 3 checks must pass to be considered healthy.
    Returns dict with status and pass/fail details.
    """
    result = {
        "label": device_label,
        "serial": full_serial,
        "online": True,
        "inject_ok": False,
        "portal_ok": False,
        "status": "unknown",
    }

    # Check 1: USB debugging (Security Settings) — input injection
    inject_ok = check_inject(full_serial)
    result["inject_ok"] = inject_ok

    # Check 2 & 3: Portal installed + accessibility enabled
    try:
        device = await adb.device(serial=full_serial)
        packages = await device.list_packages()
        is_installed = PORTAL_PACKAGE_NAME in packages
        a11y_ok = await check_portal_accessibility(device)
        result["portal_ok"] = is_installed and a11y_ok

        if assign_mode:
            # In assign mode: report all issues clearly, no install attempt
            issues = []
            if not inject_ok:
                issues.append("USB debugging (Security Settings) OFF")
            if not is_installed:
                issues.append("Portal NOT installed")
            elif not a11y_ok:
                issues.append("Portal accessibility NOT enabled")

            if not issues:
                print(f"  ✅ {device_label} ({full_serial[:20]}) — all checks passed → assigned")
                result["status"] = "ok"
            else:
                print(f"  ❌ {device_label} ({full_serial[:20]}) — SKIPPED: {', '.join(issues)}")
                result["status"] = "failed"
            return result

        # Normal check / install mode
        if is_installed and a11y_ok:
            inject_icon = "✅" if inject_ok else "⚠️ "
            inject_note = "" if inject_ok else " (USB injection needs fixing — run check_devices.py)"
            print(f"  ✅ {device_label} ({full_serial[:20]}) — Portal OK {inject_icon}{inject_note}")
            result["status"] = "ok"
            return result

        status_str = "Portal NOT installed" if not is_installed else "Portal accessibility NOT enabled"

        if not install:
            print(f"  ❌ {device_label} ({full_serial[:20]}) — {status_str}")
            result["status"] = "needs_setup"
            return result

        print(f"  🔧 {device_label} ({full_serial[:20]}) — {status_str} → installing...")
        success = await setup_portal(device, debug=False)
        if success:
            print(f"  ✅ {device_label} — Portal installed and accessibility enabled")
            result["portal_ok"] = True
            result["status"] = "installed"
        else:
            print(f"  ⚠️  {device_label} — Install attempted but failed (may need manual action on device)")
            print(f"       → On the device: Settings → Developer Options → 'Install unknown apps'")
            print(f"       → Or enable: Settings → Accessibility → DroidRun Portal")
            result["status"] = "failed"

    except Exception as e:
        print(f"  ❌ {device_label} ({full_serial[:20]}) — Error: {e}")
        result["status"] = "error"
        result["detail"] = str(e)

    return result


async def main(install=True, assign_mode=False):
    transport_map = get_transport_map()
    online_transports = set(transport_map.values())

    mode_label = "Assign Healthy Devices" if assign_mode else ("Check + Install" if install else "Check Only")
    print()
    print("=" * 65)
    print(f"  DroidRun Portal Setup — {mode_label}")
    print("=" * 65)

    tasks = []
    skipped = []

    for label, short_serial in DEVICE_POOL.items():
        full_serial = transport_map.get(short_serial, short_serial)
        if full_serial not in online_transports and full_serial == short_serial:
            skipped.append(label)
            continue
        tasks.append(check_device(label, full_serial, install=install, assign_mode=assign_mode))

    if skipped:
        print(f"\n  ⚠️  Offline / not found (skipped): {', '.join(skipped)}")

    print()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    results = [r for r in results if isinstance(r, dict)]

    ok = sum(1 for r in results if r.get("status") in ("ok", "installed"))
    failed = sum(1 for r in results if r.get("status") not in ("ok", "installed"))

    print()
    print("=" * 65)
    print(f"  Results: {ok} ready, {failed} need attention, {len(skipped)} offline")
    print("=" * 65)

    if assign_mode:
        # Write only fully passing devices to active_devices.json
        active = {
            r["label"]: {"serial": DEVICE_POOL[r["label"]]}
            for r in results
            if r.get("status") == "ok"
        }
        with open(ACTIVE_DEVICES_FILE, "w") as f:
            json.dump(active, f, indent=2)

        print()
        print(f"  📋 {len(active)} devices assigned → active_devices.json")
        if active:
            for label in active:
                print(f"     • {label} ({DEVICE_POOL[label]})")
        if failed or skipped:
            excluded = [r["label"] for r in results if r.get("status") != "ok"] + skipped
            print(f"\n  ⛔  {len(excluded)} excluded: {', '.join(excluded)}")
            print("      Fix issues above, then re-run: python setup_devices.py --assign")
        print()
        return

    if failed > 0 and install:
        print()
        print("  Manual steps for failed devices:")
        print("  1. Settings → Developer Options → 'Install from unknown sources'")
        print("     or 'Disable permission monitoring' (OPPO/Realme)")
        print("  2. Re-run: python setup_devices.py")
        print("  3. Enable Accessibility: Settings → Accessibility → DroidRun Portal")
    print()


if __name__ == "__main__":
    assign_mode = "--assign" in sys.argv
    install = "--check" not in sys.argv and not assign_mode
    asyncio.run(main(install=install, assign_mode=assign_mode))
