"""
AEO Local Flows — runs directly ON the Android device via Termux+Shizuku.
All ADB commands become local shell commands (no 'adb -s serial' prefix).
"""

import json
import os
import re
import subprocess
import time


CHROME_PACKAGE = "com.android.chrome"
GENERATION_TIMEOUT = 180
GENERATION_POLL = 3
KEYBOARD_HIDE_WAIT = 10


def sh(cmd, timeout=10):
    """Run a local shell command on the device."""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.stdout


def tap(x, y):
    sh(f"input tap {x} {y}")
    time.sleep(0.5)


def swipe(x1, y1, x2, y2, duration=700):
    sh(f"input swipe {x1} {y1} {x2} {y2} {duration}")


def press_enter():
    sh("input keyevent 66")


def press_back():
    sh("input keyevent 4")


def type_text(text):
    """Type text word by word with spaces between."""
    safe = text.replace("'", "").replace('"', '').replace("&", "and")
    safe = safe.replace(";", "").replace("|", "").replace("`", "")
    safe = safe.replace("$", "").replace("(", "").replace(")", "")
    words = safe.split()
    for i, word in enumerate(words):
        w = re.sub(r'[^a-zA-Z0-9.,!?/@#%*+=\-:]', '', word)
        if w:
            sh(f"input text '{w}'")
        if i < len(words) - 1:
            sh("input keyevent 62")
        time.sleep(0.03)


def adb_screen_size():
    """Get screen width and height."""
    out = sh("wm size")
    m = re.search(r'(\d+)x(\d+)', out)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 720, 1600


def find_element(text=None, resource_id=None, content_desc=None):
    """Find element via uiautomator dump."""
    sh("uiautomator dump /sdcard/ui_dump.xml", timeout=15)
    xml = sh("cat /sdcard/ui_dump.xml", timeout=5)
    if not xml:
        return None

    pattern = r'<node[^>]*'
    if text:
        pattern += f'text="{re.escape(text)}"[^>]*'
    if resource_id:
        pattern += f'resource-id="[^"]*{re.escape(resource_id)}"[^>]*'
    if content_desc:
        pattern += f'content-desc="[^"]*{re.escape(content_desc)}"[^>]*'
    pattern += r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'

    m = re.search(pattern, xml)
    if m:
        x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        return cx, cy
    return None


def find_and_tap(text=None, resource_id=None, content_desc=None):
    """Find element and tap it. Returns True if found."""
    pos = find_element(text=text, resource_id=resource_id, content_desc=content_desc)
    if pos:
        tap(pos[0], pos[1])
        return True
    return False


def screencap(path):
    """Take screenshot."""
    sh(f"screencap -p {path}")


def force_stop_chrome():
    sh(f"am force-stop {CHROME_PACKAGE}")


def launch_url(url):
    sh(f'am start --activity-clear-task -a android.intent.action.VIEW -d "{url}" {CHROME_PACKAGE}')


def dismiss_chrome_fre():
    """Dismiss Chrome First Run Experience."""
    dismiss_texts = [
        "Use without an account", "Stay signed out",
        "No thanks", "Got it", "Accept & continue", "Accept",
    ]
    for attempt in range(10):
        found_any = False
        for t in dismiss_texts:
            if find_and_tap(text=t):
                time.sleep(1)
                found_any = True
                break
        if not found_any:
            if find_element(resource_id="search_box_text") or find_element(resource_id="url_bar"):
                break
        time.sleep(0.5)


def wait_for_page_ready(platform, timeout=45):
    """Wait until platform page is loaded."""
    indicators = {
        "gemini": ["Send message", "Ask Gemini"],
        "chatgpt": ["Ask anything", "prompt-textarea", "Send prompt"],
        "perplexity": ["Ask anything", "ask-input"],
    }
    targets = indicators.get(platform.lower(), [])
    start = time.time()
    while time.time() - start < timeout:
        for t in targets:
            if find_element(text=t) or find_element(resource_id=t) or find_element(content_desc=t):
                return True
        time.sleep(2)
    return False


def wait_for_generation(timeout=180):
    """Wait for AI generation to complete."""
    time.sleep(5)
    no_stop_count = 0
    start = time.time()
    while time.time() - start < timeout:
        sh("uiautomator dump /sdcard/ui_dump.xml", timeout=15)
        xml = sh("cat /sdcard/ui_dump.xml", timeout=5)
        has_stop = any(s in xml for s in ["Stop streaming", "Stop generating", "Stop response"])
        if has_stop:
            no_stop_count = 0
            time.sleep(GENERATION_POLL)
            continue
        has_done = any(s in xml for s in ["Copy", "Share", "Read aloud", "Sources", "Try again"])
        if has_done:
            time.sleep(2)
            return True
        no_stop_count += 1
        if no_stop_count >= 3:
            return False
        time.sleep(GENERATION_POLL)
    return False


def adb_scroll_up(count=12):
    """Scroll up to see the response from the top."""
    w, h = adb_screen_size()
    for _ in range(count):
        swipe(int(w * 0.3), int(h * 0.3), int(w * 0.3), int(h * 0.7), 500)
        time.sleep(0.3)


def adb_scroll_down(count=1):
    """Scroll down."""
    w, h = adb_screen_size()
    for _ in range(count):
        swipe(int(w * 0.3), int(h * 0.7), int(w * 0.3), int(h * 0.3), 700)
        time.sleep(0.5)


# ── CDP Helpers (local on device) ────────────────────────────────────────────

def cdp_connect(local_port=9222):
    """Connect to Chrome DevTools Protocol locally on device."""
    import json as _json
    try:
        out = sh(f"curl -s http://localhost:{local_port}/json", timeout=5)
        tabs = _json.loads(out) if out else []
        for tab in tabs:
            ws_url = tab.get("webSocketDebuggerUrl")
            if ws_url:
                return ws_url
    except Exception:
        pass
    return None


def cdp_eval(ws_url, js_expr):
    """Evaluate JS via CDP WebSocket (using python websocket-client)."""
    try:
        import websocket
        import json as _json
        ws = websocket.create_connection(ws_url, timeout=10)
        msg = _json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": js_expr}})
        ws.send(msg)
        result = _json.loads(ws.recv())
        ws.close()
        value = result.get("result", {}).get("result", {}).get("value")
        return value
    except Exception:
        return None


def extract_response_text_cdp(platform, cdp_port=9222):
    """Extract response text via CDP JS selectors."""
    ws_url = cdp_connect(cdp_port)
    if not ws_url:
        return ""

    js_map = {
        "gemini": """
            (() => {
                let el = document.querySelector('model-response, message-content, .model-response-text');
                if (!el) {
                    let all = document.querySelectorAll('[class*="response"], [class*="model"]');
                    for (let a of all) { if (a.innerText && a.innerText.length > 50) { el = a; break; } }
                }
                return el ? el.innerText : document.body.innerText;
            })()
        """,
        "chatgpt": """
            (() => {
                let el = document.querySelector('[data-message-author-role="assistant"]');
                return el ? el.innerText : document.body.innerText;
            })()
        """,
        "perplexity": """
            (() => {
                let el = document.querySelector('[class*="answer"], .prose');
                return el ? el.innerText : document.body.innerText;
            })()
        """,
    }
    js = js_map.get(platform.lower(), js_map["gemini"])
    return cdp_eval(ws_url, js) or ""


def scroll_response_to_top_cdp(platform, cdp_port=9222):
    """Scroll response to top via CDP."""
    ws_url = cdp_connect(cdp_port)
    if not ws_url:
        adb_scroll_up(5)
        return

    js_map = {
        "gemini": """
            (() => {
                let el = document.querySelector('model-response, message-content, .model-response-text');
                if (!el) { let all = document.querySelectorAll('[class*="response"]'); for (let a of all) { if (a.innerText && a.innerText.length > 50) { el = a; break; } } }
                if (el) { el.scrollIntoView({behavior:'instant',block:'start'}); window.scrollBy(0,-150); return true; }
                return false;
            })()
        """,
        "chatgpt": """
            (() => {
                let el = document.querySelector('[data-message-author-role="assistant"], article');
                if (el) { el.scrollIntoView({behavior:'instant',block:'start'}); window.scrollBy(0,-150); return true; }
                return false;
            })()
        """,
        "perplexity": """
            (() => {
                let el = document.querySelector('.prose, [class*="answer-content"]');
                if (!el) el = document.querySelector('ol, [class*="answer"]');
                if (el) { el.scrollIntoView({behavior:'instant',block:'start'}); window.scrollBy(0,-120); return true; }
                return false;
            })()
        """,
    }
    js = js_map.get(platform.lower(), js_map["gemini"])
    cdp_eval(ws_url, js)


def set_zoom_cdp(zoom=0.75, cdp_port=9222):
    """Set page zoom via CDP."""
    ws_url = cdp_connect(cdp_port)
    if ws_url:
        cdp_eval(ws_url, f"document.body.style.zoom = '{zoom}'")


# ── Platform Flows ───────────────────────────────────────────────────────────

def run_gemini(prompt, output_dir="audit_results", is_first=True):
    """Run Gemini audit flow locally on device."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    os.makedirs(f"{output_dir}/Gemini", exist_ok=True)
    ss_path = f"{output_dir}/Gemini/gemini_{timestamp}.png"
    text_path = f"{output_dir}/Gemini/gemini_{timestamp}.txt"

    print(f"[Gemini] Starting...")
    force_stop_chrome()
    time.sleep(1)

    launch_url("https://gemini.google.com")
    time.sleep(3)

    if is_first:
        dismiss_chrome_fre()
        time.sleep(1)

    print("[Gemini] Waiting for page ready...")
    wait_for_page_ready("gemini")

    if is_first:
        find_and_tap(text="No thanks")
        time.sleep(0.5)

    w, h = adb_screen_size()
    if not find_and_tap(text="Ask Gemini"):
        tap(int(w * 0.3), int(h * 0.75))
    time.sleep(1)
    find_and_tap(text="Never allow")
    time.sleep(0.5)
    if not find_and_tap(text="Ask Gemini"):
        tap(int(w * 0.3), int(h * 0.75))
    time.sleep(1)

    print("[Gemini] Typing prompt...")
    type_text(prompt)
    time.sleep(1)

    if not find_and_tap(content_desc="Send message"):
        find_and_tap(text="Send") or tap(w - 50, int(h * 0.85))
    time.sleep(2)

    print("[Gemini] Waiting for generation...")
    wait_for_generation()
    time.sleep(3)

    find_and_tap(text="Close banner")
    time.sleep(1)

    print("[Gemini] Capturing screenshot...")
    scroll_response_to_top_cdp("gemini")
    set_zoom_cdp(0.75)
    time.sleep(1)
    screencap(ss_path)

    print("[Gemini] Extracting response text...")
    response = extract_response_text_cdp("gemini")
    with open(text_path, "w") as f:
        f.write(response)

    print(f"[Gemini] Done. Screenshot: {ss_path}")
    return ss_path, text_path, timestamp, response


def run_chatgpt(prompt, output_dir="audit_results", is_first=True):
    """Run ChatGPT audit flow locally on device."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    os.makedirs(f"{output_dir}/ChatGPT", exist_ok=True)
    ss_path = f"{output_dir}/ChatGPT/chatgpt_{timestamp}.png"
    text_path = f"{output_dir}/ChatGPT/chatgpt_{timestamp}.txt"

    print(f"[ChatGPT] Starting...")
    force_stop_chrome()
    time.sleep(1)

    launch_url("https://chatgpt.com")
    time.sleep(3)

    if is_first:
        dismiss_chrome_fre()
        time.sleep(1)

    print("[ChatGPT] Waiting for page ready...")
    wait_for_page_ready("chatgpt")

    w, h = adb_screen_size()
    if not find_and_tap(text="Ask anything"):
        if not find_and_tap(resource_id="prompt-textarea"):
            tap(int(w * 0.5), int(h * 0.85))
    time.sleep(1)

    print("[ChatGPT] Typing prompt...")
    type_text(prompt)
    time.sleep(1)

    if not find_and_tap(content_desc="Send prompt"):
        find_and_tap(text="Send") or tap(w - 50, int(h * 0.85))
    time.sleep(2)

    print("[ChatGPT] Waiting for generation...")
    wait_for_generation()
    time.sleep(3)

    find_and_tap(content_desc="Close")
    time.sleep(1)

    print("[ChatGPT] Capturing screenshot...")
    scroll_response_to_top_cdp("chatgpt")
    set_zoom_cdp(0.75)
    time.sleep(1)
    screencap(ss_path)

    print("[ChatGPT] Extracting response text...")
    response = extract_response_text_cdp("chatgpt")
    with open(text_path, "w") as f:
        f.write(response)

    print(f"[ChatGPT] Done. Screenshot: {ss_path}")
    return ss_path, text_path, timestamp, response


def run_perplexity(prompt, output_dir="audit_results", is_first=True):
    """Run Perplexity audit flow locally on device."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    os.makedirs(f"{output_dir}/Perplexity", exist_ok=True)
    ss_path = f"{output_dir}/Perplexity/perplexity_{timestamp}.png"
    text_path = f"{output_dir}/Perplexity/perplexity_{timestamp}.txt"

    print(f"[Perplexity] Starting...")
    force_stop_chrome()
    time.sleep(1)

    launch_url("https://www.perplexity.ai")
    time.sleep(3)

    if is_first:
        dismiss_chrome_fre()
        time.sleep(1)

    # Dismiss Comet modal
    for _ in range(3):
        if not find_and_tap(text="Close"):
            press_back()
        time.sleep(1)

    print("[Perplexity] Waiting for page ready...")
    wait_for_page_ready("perplexity")

    w, h = adb_screen_size()
    if not find_and_tap(text="Ask anything"):
        if not find_and_tap(resource_id="ask-input"):
            tap(int(w * 0.5), int(h * 0.85))
    time.sleep(1)

    print("[Perplexity] Typing prompt...")
    type_text(prompt)
    time.sleep(1)

    if not find_and_tap(content_desc="Submit"):
        if not find_and_tap(content_desc="Send"):
            press_enter()
    time.sleep(2)

    print("[Perplexity] Waiting for generation...")
    wait_for_generation()
    time.sleep(3)

    print("[Perplexity] Capturing screenshot...")
    scroll_response_to_top_cdp("perplexity")
    set_zoom_cdp(0.75)
    time.sleep(1)
    screencap(ss_path)

    print("[Perplexity] Extracting response text...")
    response = extract_response_text_cdp("perplexity")
    with open(text_path, "w") as f:
        f.write(response)

    print(f"[Perplexity] Done. Screenshot: {ss_path}")
    return ss_path, text_path, timestamp, response
