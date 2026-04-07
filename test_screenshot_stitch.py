"""
Test 1: Scroll + Stitch approach
Takes multiple screenshots while scrolling, stitches into one tall image.
"""
import subprocess
import time
import os
from PIL import Image
from io import BytesIO

SERIAL = "adb-R83L103VCVH-uvv2pp._adb-tls-connect._tcp"
OUTPUT_DIR = "screenshot_test"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def adb(*args, timeout=10):
    cmd = ["adb", "-s", SERIAL] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout


def adb_raw(*args, timeout=10):
    """Return raw bytes (for screencap -p)."""
    cmd = ["adb", "-s", SERIAL] + list(args)
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return r.stdout


def screenshot():
    """Take screenshot and return PIL Image."""
    raw = adb_raw("exec-out", "screencap", "-p")
    return Image.open(BytesIO(raw))


def get_screen_size():
    import re
    output = adb("shell", "wm", "size")
    match = re.search(r'(\d+)x(\d+)', output)
    if match:
        return int(match.group(1)), int(match.group(2))
    return 720, 1600


def stitch_screenshots(images, chrome_bar_height=140, nav_bar_height=130):
    """
    Stitch multiple screenshots into one tall image.
    Crops chrome address bar from all but first,
    and nav bar from all but last.
    """
    if not images:
        return None
    if len(images) == 1:
        return images[0]

    w, h = images[0].size
    cropped = []

    for i, img in enumerate(images):
        top = 0 if i == 0 else chrome_bar_height
        bottom = h if i == len(images) - 1 else h - nav_bar_height
        cropped.append(img.crop((0, top, w, bottom)))

    total_height = sum(c.size[1] for c in cropped)
    result = Image.new("RGB", (w, total_height))

    y = 0
    for c in cropped:
        result.paste(c, (0, y))
        y += c.size[1]

    return result


def main():
    w, h = get_screen_size()
    print(f"Screen: {w}x{h}")
    print(f"Device: {SERIAL}")

    # Take screenshots while scrolling
    screenshots = []
    num_scrolls = 5  # adjust based on response length

    print(f"\nTaking {num_scrolls + 1} screenshots with scrolling...")
    for i in range(num_scrolls + 1):
        print(f"  Screenshot {i+1}...")
        img = screenshot()
        screenshots.append(img)

        if i < num_scrolls:
            # Scroll down
            start_y = int(h * 0.75)
            end_y = int(h * 0.25)
            adb("shell", "input", "swipe", "15", str(start_y), "15", str(end_y), "500")
            time.sleep(1.5)

    # Stitch
    print("\nStitching...")
    result = stitch_screenshots(screenshots)
    output_path = os.path.join(OUTPUT_DIR, "stitch_result.png")
    result.save(output_path)
    print(f"Saved: {output_path}")
    print(f"Size: {result.size[0]}x{result.size[1]}")

    # Also save individual screenshots for comparison
    for i, img in enumerate(screenshots):
        img.save(os.path.join(OUTPUT_DIR, f"frame_{i}.png"))
    print(f"Individual frames saved in {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
