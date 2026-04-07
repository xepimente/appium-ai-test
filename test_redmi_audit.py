"""Manual step-by-step audit test on Redmi — screenshot after each step."""
import subprocess
import time
import sys

SERIAL = "adb-G6TGT4SCTCYTDMDU-pUY1ol._adb-tls-connect._tcp"
PROMPT = "Top 3 businesses for bilingual childcare San Francisco. Format: numbered list, each entry: name, 2-3 sentence description of why they stand out, and whether they appear on Google Maps (yes/no). After the list, a short paragraph: is Maes Childcare (https://www.maeschildcare.com) a leader in this space? Keep entire response under 100 words."

def adb(*args, timeout=10):
    return subprocess.run(["adb", "-s", SERIAL] + list(args),
                          capture_output=True, text=True, timeout=timeout)

def screenshot(name):
    adb("shell", "screencap", "-p", "/sdcard/step.png")
    subprocess.run(["adb", "-s", SERIAL, "pull", "/sdcard/step.png", f"/tmp/redmi_{name}.png"],
                   capture_output=True)
    print(f"  📸 Screenshot saved: /tmp/redmi_{name}.png")

def wait_input(msg):
    print(f"\n{'='*60}")
    print(f"STEP: {msg}")
    print(f"{'='*60}")

# ── Step 1: Clear Chrome ──
wait_input("Clear Chrome + lock portrait")
adb("shell", "pm", "clear", "com.android.chrome")
adb("shell", "settings", "put", "system", "accelerometer_rotation", "0")
adb("shell", "settings", "put", "system", "user_rotation", "0")
time.sleep(2)
screenshot("01_chrome_cleared")
print("  Chrome cleared.")

# ── Step 2: Launch Chrome ──
wait_input("Launch Chrome")
adb("shell", "am", "start", "-n", "com.android.chrome/com.google.android.apps.chrome.Main")
time.sleep(4)
screenshot("02_chrome_launched")

# ── Step 3: Dismiss FRE ──
wait_input("Dismiss Chrome First Run Experience")
from flows_adb import dismiss_chrome_fre
dismiss_chrome_fre(SERIAL)
time.sleep(2)
screenshot("03_fre_dismissed")

# ── Step 4: Navigate to Perplexity ──
wait_input("Navigate to Perplexity")
adb("shell", "pm", "disable-user", "--user", "0", "com.android.vending", timeout=5)
adb("shell", "am", "start", "-a", "android.intent.action.VIEW",
    "-d", "https://www.perplexity.ai", "com.android.chrome")
time.sleep(8)
screenshot("04_perplexity_loaded")

# ── Step 5: Dismiss Perplexity popups ──
wait_input("Dismiss Perplexity popups")
from flows_adb import find_and_tap, dump_ui
find_and_tap(SERIAL, text="No thanks")
time.sleep(1)
# Check for overlays
xml = dump_ui(SERIAL)
if "Download now" in xml or "Get Comet" in xml or "Open in App" in xml:
    print("  Overlay detected — pressing back")
    adb("shell", "input", "keyevent", "4")
    time.sleep(2)
screenshot("05_popups_dismissed")

# ── Step 6: Check if input is visible ──
wait_input("Check for input field")
xml = dump_ui(SERIAL)
has_input = "Ask anything" in xml or "ask-input" in xml
print(f"  Input found via uiautomator: {has_input}")
if not has_input:
    print("  Checking via CDP...")
    from screenshot import cdp_connect, cdp_eval, cdp_disconnect
    ws = cdp_connect(SERIAL, 9222)
    if ws:
        result = cdp_eval(ws, "document.querySelector('[contenteditable][role=textbox]') !== null")
        print(f"  Input found via CDP: {result}")
        cdp_disconnect(SERIAL, ws, 9222)
screenshot("06_input_check")

# ── Step 7: Tap input and type ──
wait_input("Tap input field + type prompt")
if not find_and_tap(SERIAL, resource_id="ask-input"):
    if not find_and_tap(SERIAL, text="Ask anything"):
        from flows_adb import adb_screen_size, tap
        w, h = adb_screen_size(SERIAL)
        tap(SERIAL, w // 2, int(h * 0.6))
time.sleep(1)
from flows_adb import type_text
type_text(SERIAL, PROMPT)
time.sleep(1)
screenshot("07_prompt_typed")

# ── Step 8: Submit ──
wait_input("Submit query")
from flows_adb import hide_keyboard_and_submit
hide_keyboard_and_submit(SERIAL)
time.sleep(3)
screenshot("08_submitted")

# ── Step 9: Check if generating ──
wait_input("Check generation status")
xml = dump_ui(SERIAL)
has_stop = any(p in xml for p in ["Stop streaming", "Stop generating", "Stop response"])
print(f"  Generating: {has_stop}")
if not has_stop:
    print("  Pressing Enter as fallback...")
    adb("shell", "input", "keyevent", "66")
    time.sleep(2)
screenshot("09_generating")

# ── Step 10: Wait for response ──
wait_input("Wait for generation to complete")
from flows_adb import wait_for_generation as adb_wait_gen
adb_wait_gen(SERIAL)
time.sleep(3)
screenshot("10_response_done")

print("\n" + "="*60)
print("TEST COMPLETE — check /tmp/redmi_*.png files")
print("="*60)
