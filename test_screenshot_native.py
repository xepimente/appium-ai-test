"""
Test 3: Native Android scrolling screenshot
Triggers screenshot via keyevent, then taps "Capture more" to expand.
"""
import subprocess
import time
import re
import os

SERIAL = "adb-R83L103VCVH-uvv2pp._adb-tls-connect._tcp"
OUTPUT_DIR = "screenshot_test"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def adb(*args, timeout=10):
    cmd = ["adb", "-s", SERIAL] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout


def dump_ui():
    adb("shell", "uiautomator", "dump", "/sdcard/ui.xml", timeout=20)
    return adb("shell", "cat", "/sdcard/ui.xml", timeout=10)


def find_and_tap(text=None, content_desc=None):
    xml = dump_ui()
    for node in re.finditer(
        r'text="([^"]*)"[^>]*resource-id="([^"]*)"[^>]*'
        r'content-desc="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml
    ):
        t, rid, desc, x1, y1, x2, y2 = node.groups()
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
        if text and text.lower() in t.lower():
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            print(f"  Found '{t}' at ({cx},{cy}) — tapping")
            adb("shell", "input", "tap", str(cx), str(cy))
            return True
        if content_desc and content_desc.lower() in desc.lower():
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            print(f"  Found desc='{desc}' at ({cx},{cy}) — tapping")
            adb("shell", "input", "tap", str(cx), str(cy))
            return True
    return False


def main():
    print(f"Device: {SERIAL}")
    print()

    # Step 1: Trigger screenshot
    print("Step 1: Triggering screenshot (keyevent 120)...")
    adb("shell", "input", "keyevent", "120")
    time.sleep(3)

    # Step 2: Look for "Capture more" button
    print("Step 2: Looking for 'Capture more' button...")
    xml = dump_ui()

    # Print all visible text elements for debugging
    print("\n  All visible UI elements:")
    for node in re.finditer(
        r'text="([^"]*)"[^>]*resource-id="([^"]*)"[^>]*'
        r'content-desc="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml
    ):
        t, rid, desc, x1, y1, x2, y2 = node.groups()
        if t or desc:
            print(f"    text='{t}' rid='{rid}' desc='{desc}' bounds=[{x1},{y1}][{x2},{y2}]")

    # Try common button names for "Capture more"
    print("\nStep 3: Trying to tap 'Capture more'...")
    found = False
    for label in ["Capture more", "Scroll capture", "Long screenshot", "Capture More"]:
        if find_and_tap(text=label):
            found = True
            break
        if find_and_tap(content_desc=label):
            found = True
            break

    if not found:
        print("  'Capture more' button not found in UI dump.")
        print("  Check the device screen — the screenshot toolbar may look different on Samsung.")
        print("\n  Trying to find any screenshot-related buttons...")
        for node in re.finditer(
            r'text="([^"]*)"[^>]*resource-id="([^"]*)"[^>]*'
            r'content-desc="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            xml
        ):
            t, rid, desc, x1, y1, x2, y2 = node.groups()
            t_lower = (t + desc).lower()
            if any(k in t_lower for k in ["capture", "scroll", "screenshot", "more", "expand"]):
                print(f"    MATCH: text='{t}' desc='{desc}' bounds=[{x1},{y1}][{x2},{y2}]")

    if found:
        print("\nStep 4: Waiting for scroll capture to complete...")
        time.sleep(5)

        # Look for Save/Done button
        print("Looking for Save button...")
        find_and_tap(text="Save") or find_and_tap(content_desc="Save")
        time.sleep(2)

        # Pull the latest screenshot
        print("\nStep 5: Pulling screenshot from device...")
        # Samsung saves screenshots in /sdcard/DCIM/Screenshots/ or /sdcard/Pictures/Screenshots/
        ls = adb("shell", "ls", "-t", "/sdcard/DCIM/Screenshots/", timeout=5)
        if not ls.strip():
            ls = adb("shell", "ls", "-t", "/sdcard/Pictures/Screenshots/", timeout=5)
            folder = "/sdcard/Pictures/Screenshots/"
        else:
            folder = "/sdcard/DCIM/Screenshots/"

        if ls.strip():
            latest = ls.strip().split("\n")[0]
            remote = f"{folder}{latest}"
            local = os.path.join(OUTPUT_DIR, "native_fullpage.png")
            subprocess.run(
                ["adb", "-s", SERIAL, "pull", remote, local],
                capture_output=True
            )
            print(f"Saved: {local}")
        else:
            print("  Could not find screenshot file on device")
    else:
        print("\n  Native scroll screenshot not available or button not found.")
        print("  This is expected — not all devices/Android versions support it via ADB.")


if __name__ == "__main__":
    main()
