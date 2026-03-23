"""
AEO ADB-Only Flows — v1.0
--------------------------
Platform flows using only ADB commands (no Appium).
For devices where UiAutomator2/Appium crashes (e.g. Infinix X6725).

Uses:
  - adb shell input tap/swipe/text/keyevent
  - adb shell uiautomator dump (for finding elements)
  - adb shell am start (for launching apps)
"""

import re
import subprocess
import time


# ── Constants ─────────────────────────────────────────────────────────────────

CHROME_PACKAGE = "com.android.chrome"
GENERATION_TIMEOUT = 120
GENERATION_POLL = 3
KEYBOARD_HIDE_WAIT = 10


# ── ADB Helpers ───────────────────────────────────────────────────────────────

def adb(serial, *args, timeout=10):
    """Run an adb command, return stdout."""
    cmd = ["adb", "-s", serial] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout


def tap(serial, x, y):
    adb(serial, "shell", "input", "tap", str(x), str(y))
    time.sleep(0.5)


def swipe(serial, x1, y1, x2, y2, duration=700):
    adb(serial, "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration))


def press_enter(serial):
    adb(serial, "shell", "input", "keyevent", "66")


def press_back(serial):
    adb(serial, "shell", "input", "keyevent", "4")


def get_screen_size(serial):
    output = adb(serial, "shell", "wm", "size")
    match = re.search(r'(\d+)x(\d+)', output)
    if match:
        return int(match.group(1)), int(match.group(2))
    return 720, 1600


def dump_ui(serial):
    adb(serial, "shell", "uiautomator", "dump", "/sdcard/ui.xml", timeout=20)
    return adb(serial, "shell", "cat", "/sdcard/ui.xml", timeout=10)


def find_element(serial, text=None, resource_id=None, content_desc=None):
    """Find element in UI dump. Returns (cx, cy, text, rid) or None."""
    xml = dump_ui(serial)
    for node in re.finditer(
        r'text="([^"]*)"[^>]*resource-id="([^"]*)"[^>]*'
        r'content-desc="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml
    ):
        t, rid, desc, x1, y1, x2, y2 = node.groups()
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
        matched = False
        if text and text in t:
            matched = True
        if resource_id and resource_id in rid:
            matched = True
        if content_desc and content_desc in desc:
            matched = True
        if matched:
            return (x1 + x2) // 2, (y1 + y2) // 2, t, rid
    return None


def find_and_tap(serial, text=None, resource_id=None, content_desc=None, label=""):
    result = find_element(serial, text=text, resource_id=resource_id, content_desc=content_desc)
    if result:
        cx, cy, t, rid = result
        tap(serial, cx, cy)
        return True
    return False


# ── Chrome Setup ──────────────────────────────────────────────────────────────

def clear_chrome(serial):
    """Clear Chrome and lock portrait."""
    adb(serial, "shell", "pm", "clear", CHROME_PACKAGE, timeout=15)
    adb(serial, "shell", "settings", "put", "system", "accelerometer_rotation", "0")
    adb(serial, "shell", "settings", "put", "system", "user_rotation", "0")


def dismiss_chrome_fre(serial):
    """Dismiss Chrome first-run dialogs via UI dump + tap."""
    print("  Dismissing Chrome FRE...")
    for attempt in range(6):
        time.sleep(2)
        xml = dump_ui(serial)

        if "signin_fre_dismiss_button" in xml or "Use without an account" in xml:
            print("    Tapping: Use without an account")
            find_and_tap(serial, resource_id="signin_fre_dismiss_button")
            continue
        if "Stay signed out" in xml:
            print("    Tapping: Stay signed out")
            find_and_tap(serial, text="Stay signed out")
            continue
        if "ack_button" in xml or "Enhanced ad privacy" in xml:
            print("    Tapping: Got it (ad privacy)")
            find_and_tap(serial, resource_id="ack_button")
            continue
        if "No thanks" in xml:
            print("    Tapping: No thanks")
            find_and_tap(serial, text="No thanks")
            continue
        if "negative_button" in xml:
            print("    Tapping: negative_button")
            find_and_tap(serial, resource_id="negative_button")
            continue
        if "Accept" in xml:
            print("    Tapping: Accept")
            find_and_tap(serial, text="Accept")
            continue
        if "search_box_text" in xml or "url_bar" in xml:
            print("    FRE done — address bar visible")
            break
    print("  FRE complete")


def navigate_to_url(serial, url):
    """Tap address bar, type URL, press Enter."""
    print(f"  Navigating to {url}...")
    if not find_and_tap(serial, resource_id="search_box_text"):
        find_and_tap(serial, resource_id="url_bar")
    time.sleep(1)
    adb(serial, "shell", "input", "text", url, timeout=10)
    time.sleep(0.3)
    press_enter(serial)
    print("  Waiting for page to load...")
    time.sleep(8)


# ── Text Input ────────────────────────────────────────────────────────────────

def type_text(serial, text):
    """Type text word by word with proper space handling."""
    words = text.split()
    for i, word in enumerate(words):
        safe = word.replace("'", "").replace('"', '').replace("&", "and") \
                   .replace(";", "").replace("|", "").replace("(", "") \
                   .replace(")", "").replace("--", "-")
        if safe:
            adb(serial, "shell", "input", "text", safe, timeout=5)
        if i < len(words) - 1:
            adb(serial, "shell", "input", "keyevent", "62")  # SPACE
        time.sleep(0.02)


def hide_keyboard_and_submit(serial):
    """Hide keyboard, wait until Submit is at correct position, then tap it."""
    print("  Hiding keyboard...")
    hide_keyboard(serial)
    print("  Waiting for Submit to be ready...")
    for attempt in range(15):
        result = find_element(serial, text="Submit")
        if result:
            cx, cy, _, _ = result
            if cy > 1400:
                print(f"    Submit ready at ({cx},{cy}) — tapping")
                tap(serial, cx, cy)
                time.sleep(1)
                return
            else:
                print(f"    Submit at ({cx},{cy}) — keyboard still hiding... ({attempt+1}s)")
                time.sleep(1)
        else:
            print(f"    Submit not found yet... ({attempt+1}s)")
            time.sleep(1)
    print("    Fallback: tapping (638, 1494)")
    tap(serial, 638, 1494)
    time.sleep(1)


# ── Scrolling ─────────────────────────────────────────────────────────────────

def hide_keyboard(serial):
    """Hide keyboard by tapping empty content area above input."""
    w, h = get_screen_size(serial)
    # Tap in the middle of the page content (above keyboard and input)
    tap(serial, w // 2, int(h * 0.3))
    time.sleep(2)


def adb_scroll(serial, swipes=12, px=400, duration_ms=700, pause_s=2.2):
    """Scroll via ADB. Hides keyboard first, uses left edge to avoid map widgets."""
    hide_keyboard(serial)
    w, h = get_screen_size(serial)
    start_y = int(h * 0.7)
    end_y = start_y - px
    for i in range(swipes):
        swipe(serial, 15, start_y, 15, end_y, duration_ms)
        time.sleep(pause_s)


# ── Wait for Generation ───────────────────────────────────────────────────────

def wait_for_generation(serial, max_wait=GENERATION_TIMEOUT):
    """Wait until AI finishes generating by checking for Stop button."""
    time.sleep(5)
    start = time.time()
    while time.time() - start < max_wait:
        xml = dump_ui(serial)
        has_stop = False
        for pattern in ["Stop streaming", "Stop generating", "Stop response"]:
            if pattern in xml:
                has_stop = True
                break
        if not has_stop:
            time.sleep(2)
            break
        time.sleep(GENERATION_POLL)


# ── Platform Flows ─────────────────────────────────────────────────────────────

def run_gemini(serial, prompt, follow_up=None):
    steps = []
    w, h = get_screen_size(serial)

    dismiss_chrome_fre(serial)
    steps.append("dismissed_fre")

    navigate_to_url(serial, "gemini.google.com")
    steps.append("navigated")

    time.sleep(2)
    find_and_tap(serial, text="No thanks")
    time.sleep(1)

    # Tap input area
    input_y = int(h * 0.85)
    tap(serial, w // 2, input_y)
    time.sleep(1)

    type_text(serial, prompt)
    steps.append("typed_prompt")
    time.sleep(1)

    # Find and tap Send (works with keyboard open)
    if not find_and_tap(serial, content_desc="Send message"):
        find_and_tap(serial, text="Send") or tap(serial, w - 50, input_y)
    steps.append("sent_prompt")

    wait_for_generation(serial)
    steps.append("generation_complete")

    adb_scroll(serial)
    steps.append("scrolled")

    if follow_up and follow_up.strip():
        time.sleep(2)
        tap(serial, w // 2, input_y)
        time.sleep(1)
        type_text(serial, follow_up)
        time.sleep(1)
        # Find and tap Send again
        if not find_and_tap(serial, content_desc="Send message"):
            find_and_tap(serial, text="Send") or tap(serial, w - 50, input_y)
        steps.append("sent_followup")
        wait_for_generation(serial)
        adb_scroll(serial)
        steps.append("scrolled_followup")

    return {"status": "success", "steps": steps}


def run_chatgpt(serial, prompt, follow_up=None):
    steps = []
    w, h = get_screen_size(serial)

    dismiss_chrome_fre(serial)
    steps.append("dismissed_fre")

    navigate_to_url(serial, "chatgpt.com")
    steps.append("navigated")

    time.sleep(5)

    # Find input
    if not find_and_tap(serial, resource_id="prompt-textarea"):
        if not find_and_tap(serial, text="Ask anything"):
            tap(serial, w // 2, int(h * 0.85))
    steps.append("found_input")
    time.sleep(1)

    type_text(serial, prompt)
    steps.append("typed_prompt")
    time.sleep(1)

    # Send
    if not find_and_tap(serial, resource_id="composer-submit-button"):
        find_and_tap(serial, content_desc="Send prompt") or find_and_tap(serial, text="Send")
    steps.append("sent_prompt")

    wait_for_generation(serial)
    steps.append("generation_complete")

    adb_scroll(serial)
    steps.append("scrolled")

    if follow_up and follow_up.strip():
        time.sleep(2)
        find_and_tap(serial, resource_id="prompt-textarea") or \
            find_and_tap(serial, text="Ask anything")
        time.sleep(1)
        type_text(serial, follow_up)
        time.sleep(0.5)
        find_and_tap(serial, resource_id="composer-submit-button") or \
            find_and_tap(serial, content_desc="Send prompt")
        steps.append("sent_followup")
        wait_for_generation(serial)
        adb_scroll(serial)
        steps.append("scrolled_followup")

    return {"status": "success", "steps": steps}


def run_perplexity(serial, prompt, follow_up=None):
    steps = []
    w, h = get_screen_size(serial)

    dismiss_chrome_fre(serial)
    steps.append("dismissed_fre")

    navigate_to_url(serial, "www.perplexity.ai")
    steps.append("navigated")

    # Dismiss Comet modals by tapping X
    print("  Dismissing Comet modals...")
    time.sleep(3)
    x_pos = int(w * 0.943)
    y_pos = int(h * 0.134)
    for attempt in range(2):
        time.sleep(3)
        print(f"    Tapping Comet X #{attempt+1} at ({x_pos},{y_pos})")
        tap(serial, x_pos, y_pos)
        time.sleep(2)
    time.sleep(1)

    # Find input
    print("  Looking for chat input...")
    if not find_and_tap(serial, resource_id="ask-input"):
        if not find_and_tap(serial, text="Ask anything"):
            tap(serial, w // 2, int(h * 0.6))
    steps.append("found_input")
    time.sleep(1)

    print("  Typing prompt...")
    type_text(serial, prompt)
    steps.append("typed_prompt")
    time.sleep(1)

    # Hide keyboard + Submit
    hide_keyboard_and_submit(serial)
    steps.append("sent_prompt")

    print("  Waiting for generation...")
    wait_for_generation(serial)
    steps.append("generation_complete")

    print("  Scrolling...")
    adb_scroll(serial)
    steps.append("scrolled")

    if follow_up and follow_up.strip():
        print("  Typing follow-up...")
        time.sleep(2)
        find_and_tap(serial, resource_id="ask-input") or \
            find_and_tap(serial, text="Ask anything")
        time.sleep(1)
        type_text(serial, follow_up)
        steps.append("typed_followup")
        time.sleep(1)
        hide_keyboard_and_submit(serial)
        steps.append("sent_followup")
        wait_for_generation(serial)
        adb_scroll(serial)
        steps.append("scrolled_followup")

    return {"status": "success", "steps": steps}


# ── Flow Dispatcher ────────────────────────────────────────────────────────────

FLOW_MAP = {
    "Gemini":     run_gemini,
    "ChatGPT":    run_chatgpt,
    "Perplexity": run_perplexity,
}


def run_flow_adb(platform, serial, prompt, follow_up=None):
    """
    ADB-only flow dispatcher. Same interface as flows.run_flow but no Appium.
    Returns {"status": "success"/"error", "steps": [...], "error": "..."}
    """
    flow_fn = FLOW_MAP.get(platform)
    if not flow_fn:
        return {"status": "error", "error": f"Unknown platform: {platform}", "steps": []}

    try:
        return flow_fn(serial, prompt, follow_up)
    except Exception as e:
        return {"status": "error", "error": str(e), "steps": []}
