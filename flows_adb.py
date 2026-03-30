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

import json
import re
import subprocess
import time


# ── Constants ─────────────────────────────────────────────────────────────────

CHROME_PACKAGE = "com.android.chrome"
GENERATION_TIMEOUT = 180
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
    """Navigate Chrome to a URL using am start intent (works from any page state)."""
    print(f"  Navigating to {url}...")
    full_url = url if url.startswith("http") else f"https://{url}"
    adb(serial, "shell", "am", "start", "-a", "android.intent.action.VIEW",
        "-d", full_url, CHROME_PACKAGE, timeout=10)
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
    w, h = get_screen_size(serial)

    # First check if Submit/arrow is already visible without keyboard
    result = find_element(serial, text="Submit") or find_element(serial, content_desc="Submit")
    if result:
        cx, cy, _, _ = result
        # If Submit is in the top half, keyboard is not showing — tap directly
        if cy < h * 0.5:
            print(f"    Submit visible at ({cx},{cy}) — tapping directly")
            tap(serial, cx, cy)
            time.sleep(1)
            return

    print("  Hiding keyboard...")
    hide_keyboard(serial)
    print("  Waiting for Submit to be ready...")
    for attempt in range(15):
        result = find_element(serial, text="Submit") or find_element(serial, content_desc="Submit")
        if result:
            cx, cy, _, _ = result
            if cy > 1400:
                print(f"    Submit ready at ({cx},{cy}) — tapping")
                tap(serial, cx, cy)
                time.sleep(1)
                return
            elif cy < h * 0.5:
                # No keyboard layout — submit is inline with input
                print(f"    Submit inline at ({cx},{cy}) — tapping")
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

def _has_response_content(xml):
    """Check if the UI dump contains actual AI response text (not just loading)."""
    # Look for response indicators across platforms
    for pattern in [
        'content-desc="Copy"',       # Gemini/ChatGPT copy button
        'content-desc="Share"',      # Share button appears after response
        'content-desc="Read aloud"', # Gemini read aloud
        'text="Sources"',            # Sources button = response done
        'text="Try again"',          # Error state = generation done (failed)
    ]:
        if pattern in xml:
            return True
    return False


def wait_for_generation(serial, max_wait=GENERATION_TIMEOUT):
    """
    Wait until AI finishes generating.

    Detection strategy:
    1. If Stop button visible → still generating, keep waiting
    2. If no Stop button AND response content found → done
    3. If no Stop button AND no response content → still loading, keep waiting
    4. If max_wait reached → timeout, return False

    Returns True if response loaded, False if timed out.
    """
    time.sleep(5)
    start = time.time()
    no_stop_count = 0

    while time.time() - start < max_wait:
        elapsed = int(time.time() - start)
        xml = dump_ui(serial)

        has_stop = any(p in xml for p in [
            "Stop streaming", "Stop generating", "Stop response"
        ])

        if has_stop:
            no_stop_count = 0
            if elapsed % 15 == 0:
                print(f"    Still generating... ({elapsed}s)")
            time.sleep(GENERATION_POLL)
            continue

        # No stop button — check if response content exists
        if _has_response_content(xml):
            print(f"    Generation complete ({elapsed}s)")
            time.sleep(2)
            return True

        # No stop button, no response content — might still be loading
        no_stop_count += 1
        if no_stop_count >= 3:
            # 3 checks with no stop and no content — assume page didn't load
            print(f"    No response detected after {elapsed}s — possible timeout")
            return False

        if elapsed % 10 == 0:
            print(f"    Waiting for response... ({elapsed}s)")
        time.sleep(GENERATION_POLL)

    print(f"    Generation timed out after {max_wait}s")
    return False


# ── Backlink Click (Type 3) ────────────────────────────────────────────────────

def click_backlink_adb(serial, backlinks, cdp_port=9222):
    """
    Find and click a matching backlink in the AI response sources.

    Strategy: CDP first (can scan entire page DOM including off-screen content),
    then fall back to UI dump if CDP fails.

    After clicking, stays on the backlink page for ~5 seconds with scrolling.

    Args:
        serial: ADB device serial
        backlinks: list of backlink URLs to look for
        cdp_port: CDP port for this device

    Returns:
        The matched URL string, or None if no match found.
    """
    if not backlinks:
        return None

    import random
    from screenshot import cdp_connect, cdp_disconnect, cdp_eval

    target = random.choice(backlinks)
    # Extract domain for matching (e.g., "maeschildcare.com")
    domain = target.split("//")[-1].split("/")[0].replace("www.", "")
    w, h = get_screen_size(serial)
    print(f"  Looking for backlink matching: {domain}")

    backlinks_js = json.dumps(backlinks)

    # ── Step 1: CDP scan entire page for matching links (no sources button needed) ──
    print("    Scanning entire page DOM for backlink via CDP...")
    ws = cdp_connect(serial, cdp_port)
    if ws:
        try:
            matched = cdp_eval(ws, f"""
                (() => {{
                    let backlinks = {backlinks_js};
                    let domain = "{domain}";
                    let links = document.querySelectorAll('a[href]');

                    // Pass 1: exact path match
                    for (let link of links) {{
                        let href = link.href || '';
                        if (!href.includes(domain)) continue;
                        for (let bl of backlinks) {{
                            try {{
                                let blPath = new URL(bl).pathname;
                                let linkPath = new URL(href).pathname;
                                if (blPath.length > 1 && linkPath === blPath) {{
                                    window.location.href = href;
                                    return {{url: href, match: 'exact_path'}};
                                }}
                            }} catch(e) {{}}
                        }}
                    }}

                    // Pass 2: path segment match
                    for (let link of links) {{
                        let href = link.href || '';
                        if (!href.includes(domain)) continue;
                        for (let bl of backlinks) {{
                            let parts = bl.split('/').filter(s => s);
                            let lastPart = parts[parts.length - 1] || '';
                            if (lastPart && lastPart.length > 3 && href.includes(lastPart)) {{
                                window.location.href = href;
                                return {{url: href, match: 'path_segment'}};
                            }}
                        }}
                    }}

                    // Pass 3: any link with matching domain
                    for (let link of links) {{
                        let href = link.href || '';
                        if (href.includes(domain)) {{
                            window.location.href = href;
                            return {{url: href, match: 'domain'}};
                        }}
                    }}

                    return null;
                }})()
            """)
        finally:
            cdp_disconnect(serial, ws, cdp_port)

        if matched:
            print(f"    Backlink found in page ({matched.get('match')}): {matched.get('url', '')[:60]}")
            print(f"  Waiting for backlink page to load...")
            time.sleep(8)
            print(f"  Browsing backlink page for ~5 seconds...")
            for _ in range(2):
                swipe(serial, 15, int(h * 0.7), 15, int(h * 0.4), 500)
                time.sleep(1.5)
            time.sleep(2)
            return matched.get("url", domain)
        print("    No backlink in page DOM — trying Sources panel...")

    # ── Step 2: Open Sources panel, then search again ──
    # Sources button may reveal hidden links (Gemini drawer, Perplexity citations)
    print("  Looking for Sources button...")
    sources_found = False
    for attempt in range(4):
        xml = dump_ui(serial)
        for node in re.finditer(
            r'text="([^"]*)"[^>]*resource-id="([^"]*)"[^>]*'
            r'content-desc="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            xml
        ):
            t, rid, desc, x1, y1, x2, y2 = node.groups()
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
            combined = f"{t} {desc}".lower()
            is_sources = "sources" in combined or "source" in combined
            # Perplexity: small numbered element (citation count) in lower screen
            is_citation_count = (t.strip().isdigit() and int(t.strip()) > 0
                                 and (x2 - x1) < 100 and y1 > h * 0.4)
            if is_sources or is_citation_count:
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                if cy > h * 0.3:
                    print(f"    Found Sources '{t}' at ({cx},{cy}) — tapping")
                    tap(serial, cx, cy)
                    sources_found = True
                    time.sleep(3)
                    break
        if sources_found:
            break
        if attempt < 3:
            swipe(serial, 15, int(h * 0.6), 15, int(h * 0.3), 500)
            time.sleep(1)

    if not sources_found:
        # Try CDP to open sources
        ws_src = cdp_connect(serial, cdp_port)
        if ws_src:
            try:
                cdp_eval(ws_src, r"""
                    (() => {
                        let els = document.querySelectorAll('a, button, [role=button], span');
                        for (let el of els) {
                            let text = el.textContent.trim();
                            if (/^\d+$/.test(text) && parseInt(text) > 3 && parseInt(text) < 50) {
                                let rect = el.getBoundingClientRect();
                                if (rect.width < 60 && rect.width > 10 && rect.y > 300) {
                                    el.click();
                                    return 'clicked_citation';
                                }
                            }
                        }
                        let btns = document.querySelectorAll('button, [role=button], a');
                        for (let btn of btns) {
                            if (btn.textContent && btn.textContent.trim().toLowerCase().includes('source')) {
                                btn.click();
                                return 'clicked_sources';
                            }
                        }
                        return false;
                    })()
                """)
                sources_found = True
            finally:
                cdp_disconnect(serial, ws_src, cdp_port)

    if not sources_found:
        print("    Sources button not found — no backlink to click")
        return None

    # Wait for sources panel to fully render
    time.sleep(4)

    # ── Step 3: Search sources panel for matching backlink via CDP ──
    ws2 = cdp_connect(serial, cdp_port)
    if not ws2:
        print(f"    CDP reconnect failed")
        return None

    try:
        print("    Searching sources panel for matching backlink...")
        matched = cdp_eval(ws2, f"""
            (() => {{
                let backlinks = {backlinks_js};
                let domain = "{domain}";
                let links = document.querySelectorAll('a[href]');

                // Pass 1: exact path match
                for (let link of links) {{
                    let href = link.href || '';
                    if (!href.includes(domain)) continue;
                    for (let bl of backlinks) {{
                        try {{
                            let blPath = new URL(bl).pathname;
                            let linkPath = new URL(href).pathname;
                            if (blPath.length > 1 && linkPath === blPath) {{
                                window.location.href = href;
                                return {{url: href, match: 'exact_path'}};
                            }}
                        }} catch(e) {{}}
                    }}
                }}

                // Pass 2: path segment match
                for (let link of links) {{
                    let href = link.href || '';
                    if (!href.includes(domain)) continue;
                    for (let bl of backlinks) {{
                        let parts = bl.split('/').filter(s => s);
                        let lastPart = parts[parts.length - 1] || '';
                        if (lastPart && lastPart.length > 3 && href.includes(lastPart)) {{
                            window.location.href = href;
                            return {{url: href, match: 'path_segment'}};
                        }}
                    }}
                }}

                // Pass 3: any link with matching domain
                for (let link of links) {{
                    let href = link.href || '';
                    if (href.includes(domain)) {{
                        window.location.href = href;
                        return {{url: href, match: 'domain'}};
                    }}
                }}

                return null;
            }})()
        """)
    finally:
        cdp_disconnect(serial, ws2, cdp_port)

    if matched:
        print(f"    Backlink found ({matched.get('match')}): {matched.get('url', '')[:60]}")
        print(f"  Waiting for backlink page to load...")
        time.sleep(8)
        print(f"  Browsing backlink page for ~5 seconds...")
        for _ in range(2):
            swipe(serial, 15, int(h * 0.7), 15, int(h * 0.4), 500)
            time.sleep(1.5)
        time.sleep(2)
        return matched.get("url", domain)
    else:
        print(f"    No matching backlink found (looking for {domain})")
        return None


# ── Platform Flows ─────────────────────────────────────────────────────────────

def run_gemini(serial, prompt, follow_up=None, backlinks=None):
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
    print("  Tapping input area...")
    input_y = int(h * 0.85)
    tap(serial, w // 2, input_y)
    time.sleep(1)

    print(f"  Typing prompt ({len(prompt)} chars)...")
    type_text(serial, prompt)
    steps.append("typed_prompt")
    time.sleep(1)

    # Find and tap Send (works with keyboard open)
    print("  Sending prompt...")
    if not find_and_tap(serial, content_desc="Send message"):
        find_and_tap(serial, text="Send") or tap(serial, w - 50, input_y)
    steps.append("sent_prompt")

    print("  Waiting for generation...")
    gen_ok = wait_for_generation(serial)
    if not gen_ok:
        steps.append("generation_timeout")
        return {"status": "error", "error": "Generation timed out", "steps": steps}
    steps.append("generation_complete")

    print("  Scrolling...")
    adb_scroll(serial)
    steps.append("scrolled")

    if follow_up and follow_up.strip():
        print("  Typing follow-up...")
        time.sleep(2)
        tap(serial, w // 2, input_y)
        time.sleep(1)
        type_text(serial, follow_up)
        time.sleep(1)
        print("  Sending follow-up...")
        if not find_and_tap(serial, content_desc="Send message"):
            find_and_tap(serial, text="Send") or tap(serial, w - 50, input_y)
        steps.append("sent_followup")
        print("  Waiting for follow-up generation...")
        fu_ok = wait_for_generation(serial)
        if not fu_ok:
            steps.append("followup_timeout")
            return {"status": "error", "error": "Follow-up generation timed out", "steps": steps}
        print("  Scrolling follow-up...")
        adb_scroll(serial)
        steps.append("scrolled_followup")

    # Backlink click (Type 3)
    if backlinks:
        matched = click_backlink_adb(serial, backlinks)
        if matched:
            steps.append(f"backlink_clicked:{matched}")

    return {"status": "success", "steps": steps}


def run_chatgpt(serial, prompt, follow_up=None, backlinks=None):
    steps = []
    w, h = get_screen_size(serial)

    # dismiss_chrome_fre(serial)
    # steps.append("dismissed_fre")

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

    gen_ok = wait_for_generation(serial)
    if not gen_ok:
        steps.append("generation_timeout")
        return {"status": "error", "error": "Generation timed out", "steps": steps}
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
        fu_ok = wait_for_generation(serial)
        if not fu_ok:
            steps.append("followup_timeout")
            return {"status": "error", "error": "Follow-up generation timed out", "steps": steps}
        adb_scroll(serial)
        steps.append("scrolled_followup")

    # Backlink click (Type 3)
    if backlinks:
        matched = click_backlink_adb(serial, backlinks)
        if matched:
            steps.append(f"backlink_clicked:{matched}")

    return {"status": "success", "steps": steps}


def run_perplexity(serial, prompt, follow_up=None, backlinks=None):
    steps = []
    w, h = get_screen_size(serial)

    # dismiss_chrome_fre(serial)
    # steps.append("dismissed_fre")

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
    gen_ok = wait_for_generation(serial)
    if not gen_ok:
        steps.append("generation_timeout")
        return {"status": "error", "error": "Generation timed out", "steps": steps}
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
        fu_ok = wait_for_generation(serial)
        if not fu_ok:
            steps.append("followup_timeout")
            return {"status": "error", "error": "Follow-up generation timed out", "steps": steps}
        adb_scroll(serial)
        steps.append("scrolled_followup")

    # Backlink click (Type 3)
    if backlinks:
        matched = click_backlink_adb(serial, backlinks)
        if matched:
            steps.append(f"backlink_clicked:{matched}")

    return {"status": "success", "steps": steps}


# ── Flow Dispatcher ────────────────────────────────────────────────────────────

FLOW_MAP = {
    "Gemini":     run_gemini,
    "ChatGPT":    run_chatgpt,
    "Perplexity": run_perplexity,
}


def run_flow_adb(platform, serial, prompt, follow_up=None, backlinks=None):
    """
    ADB-only flow dispatcher. Same interface as flows.run_flow but no Appium.
    Returns {"status": "success"/"error", "steps": [...], "error": "..."}
    """
    flow_fn = FLOW_MAP.get(platform)
    if not flow_fn:
        return {"status": "error", "error": f"Unknown platform: {platform}", "steps": []}

    try:
        return flow_fn(serial, prompt, follow_up, backlinks=backlinks)
    except Exception as e:
        return {"status": "error", "error": str(e), "steps": []}
