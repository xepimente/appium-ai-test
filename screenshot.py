"""
Screenshot Module
------------------
Takes a single screenshot after scrolling the AI response to the top
of the viewport via CDP JavaScript. Pixel-perfect positioning regardless
of screen size or response length.
"""

import json
import os
import re
import subprocess
import time
import urllib.request

import websocket


# ── ADB Helpers ──────────────────────────────────────────────────────────────

def adb_cmd(serial, *args, timeout=10):
    """Run an adb command, return stdout."""
    cmd = ["adb", "-s", serial] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout


def adb_raw(serial, *args, timeout=10):
    """Run an adb command, return raw bytes."""
    cmd = ["adb", "-s", serial] + list(args)
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return r.stdout


def get_screen_size(serial):
    """Get device screen width and height."""
    output = adb_cmd(serial, "shell", "wm", "size")
    match = re.search(r'(\d+)x(\d+)', output)
    if match:
        return int(match.group(1)), int(match.group(2))
    return 720, 1600


def take_screenshot(serial, output_path=None):
    """
    Take a single screenshot of what's currently visible on screen.
    Returns PNG bytes.
    """
    raw = adb_raw(serial, "exec-out", "screencap", "-p")
    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(raw)
    return raw


# ── CDP Helpers ──────────────────────────────────────────────────────────────

def cdp_connect(serial, local_port=9222):
    """Forward Chrome DevTools port and connect via WebSocket."""
    subprocess.run(
        ["adb", "-s", serial, "forward", f"tcp:{local_port}",
         "localabstract:chrome_devtools_remote"],
        capture_output=True
    )
    time.sleep(0.5)

    try:
        resp = urllib.request.urlopen(f"http://localhost:{local_port}/json")
        pages = json.loads(resp.read())
    except Exception as e:
        print(f"    CDP: Failed to connect — {e}")
        return None

    if not pages:
        return None

    # Prefer AI platform pages
    target = pages[0]
    for p in pages:
        url = p.get("url", "")
        if any(d in url for d in ["gemini.google", "chatgpt.com", "perplexity.ai"]):
            target = p
            break

    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return None

    try:
        return websocket.create_connection(ws_url, suppress_origin=True)
    except Exception as e:
        print(f"    CDP: WebSocket failed — {e}")
        return None


def cdp_disconnect(serial, ws, local_port=9222):
    """Close WebSocket and remove port forward."""
    if ws:
        try:
            ws.close()
        except Exception:
            pass
    subprocess.run(
        ["adb", "-s", serial, "forward", "--remove", f"tcp:{local_port}"],
        capture_output=True
    )


def cdp_eval(ws, expression):
    """Evaluate JS expression via CDP."""
    ws.send(json.dumps({
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {"expression": expression, "returnByValue": True},
    }))
    result = json.loads(ws.recv())
    try:
        return result["result"]["result"]["value"]
    except (KeyError, TypeError):
        return None


# ── Scroll Response to Top ───────────────────────────────────────────────────

# JS to scroll the AI response element to the top of the viewport
SCROLL_RESPONSE_JS = {
    "Gemini": """
        (() => {
            let el = document.querySelector(
                'model-response, message-content, .model-response-text'
            );
            if (!el) {
                let all = document.querySelectorAll('[class*="response"], [class*="model"]');
                for (let a of all) {
                    if (a.innerText && a.innerText.length > 50) { el = a; break; }
                }
            }
            if (el) {
                el.scrollIntoView({behavior: 'instant', block: 'start'});
                // Scroll up a bit to clear sticky header
                window.scrollBy(0, -150);
                return true;
            }
            return false;
        })()
    """,
    "ChatGPT": """
        (() => {
            let el = document.querySelector(
                '[data-message-author-role="assistant"], article'
            );
            if (el) {
                el.scrollIntoView({behavior: 'instant', block: 'start'});
                window.scrollBy(0, -150);
                return true;
            }
            return false;
        })()
    """,
    "Perplexity": """
        (() => {
            // Perplexity: find the answer/prose area (skip the prompt bubble)
            let el = document.querySelector('.prose, [class*="answer-content"]');
            if (!el) {
                // Fallback: find a numbered list (ol) which is the ranking
                el = document.querySelector('ol, [class*="answer"], [class*="response"]');
            }
            if (el) {
                el.scrollIntoView({behavior: 'instant', block: 'start'});
                window.scrollBy(0, -120);
                return true;
            }
            return false;
        })()
    """,
}

# JS to dismiss banners before screenshot
DISMISS_BANNER_JS = {
    "Gemini": """
        document.querySelectorAll(
            'button[aria-label="Close"], button[aria-label="Dismiss"]'
        ).forEach(btn => { if (btn.offsetParent !== null) btn.click(); });
    """,
    "ChatGPT": "",
    "Perplexity": """
        document.querySelectorAll(
            'button[aria-label="Close"], button[aria-label="Dismiss"]'
        ).forEach(btn => { if (btn.offsetParent !== null) btn.click(); });
    """,
}

# JS to extract response text
RESPONSE_TEXT_JS = {
    "Gemini": """
        (() => {
            let el = document.querySelector(
                'model-response, message-content, .model-response-text'
            );
            if (!el) {
                let all = document.querySelectorAll('[class*="response"], [class*="model"]');
                for (let a of all) {
                    if (a.innerText && a.innerText.length > 50) { el = a; break; }
                }
            }
            return el ? el.innerText : document.body.innerText;
        })()
    """,
    "ChatGPT": """
        (() => {
            let el = document.querySelector('[data-message-author-role="assistant"]');
            return el ? el.innerText : document.body.innerText;
        })()
    """,
    "Perplexity": """
        (() => {
            let el = document.querySelector('[class*="answer"], .prose');
            return el ? el.innerText : document.body.innerText;
        })()
    """,
}


def scroll_response_to_top(serial, platform, local_port=9222):
    """
    Use CDP to scroll the AI response element to the top of the viewport.
    Also dismisses any banners. Pixel-perfect on any screen size.
    """
    print(f"  Scrolling {platform} response to top via CDP...")
    ws = cdp_connect(serial, local_port)
    if not ws:
        print("    CDP failed — falling back to ADB scroll")
        # Fallback: 2 blind swipes
        w, h = get_screen_size(serial)
        for _ in range(2):
            adb_cmd(serial, "shell", "input", "swipe",
                    "15", str(int(h * 0.7)), "15", str(int(h * 0.3)), "500")
            time.sleep(1)
        return False

    try:
        # Dismiss banners first
        dismiss_js = DISMISS_BANNER_JS.get(platform, "")
        if dismiss_js:
            cdp_eval(ws, dismiss_js)
            time.sleep(0.5)

        # Scroll response to top
        scroll_js = SCROLL_RESPONSE_JS.get(platform, SCROLL_RESPONSE_JS["Gemini"])
        result = cdp_eval(ws, scroll_js)
        if result:
            print("    Response scrolled to top")
        else:
            print("    Response element not found — page may already be positioned correctly")
        time.sleep(0.5)
        return True
    finally:
        cdp_disconnect(serial, ws, local_port)


def extract_response_text(serial, platform, local_port=9222):
    """Extract the AI response text via CDP JS."""
    ws = cdp_connect(serial, local_port)
    if not ws:
        return ""
    try:
        text_js = RESPONSE_TEXT_JS.get(platform, RESPONSE_TEXT_JS["Gemini"])
        return cdp_eval(ws, text_js) or ""
    finally:
        cdp_disconnect(serial, ws, local_port)
