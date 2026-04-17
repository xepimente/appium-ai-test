"""
AEO Ranking Audit — v1.0
--------------------------
Runs AI ranking audit across Gemini, ChatGPT, Perplexity for each client/keyword.
Sends a concise ranking prompt, captures screenshot + response text.

Output structure:
  audit_results/
  ├── Gemini/
  │   └── {client_id}_{keyword_slug}_{YYYYMMDD_HHMMSS}.png
  ├── ChatGPT/
  │   └── {client_id}_{keyword_slug}_{YYYYMMDD_HHMMSS}.png
  ├── Perplexity/
  │   └── {client_id}_{keyword_slug}_{YYYYMMDD_HHMMSS}.png
  ├── text/
  │   └── {client_id}_{keyword_slug}_{platform}_{YYYYMMDD_HHMMSS}.txt
  └── audit_log.json

Usage:
  python3 audit.py --test                         # test with dummy client
  python3 audit.py --test --platform Gemini       # single platform
  python3 audit.py --clients 3                    # first 3 clients from clients.json
  python3 audit.py                                # all clients, all platforms
"""

import argparse
import csv
import json
import os
import re
import subprocess
import threading
import time

from screenshot import take_screenshot, scroll_response_to_top, extract_response_text, get_screen_size

# ── Config ────────────────────────────────────────────────────────────────────

OUTPUT_DIR = os.environ.get("AUDIT_OUTPUT_DIR", "audit_results")
LOG_FILE = os.path.join(OUTPUT_DIR, "audit_log.csv")
PLATFORMS = ["Gemini", "ChatGPT", "Perplexity"]

# Concise prompt — forces a short response that fits in 1 mobile screen
AUDIT_PROMPT_TEMPLATE = (
    "Top 3 businesses for {keyword} in {city}, {state}. "
    "Format: numbered list, each entry: name, 2-3 sentence description of why they stand out, "
    "and whether they appear on Google Maps (yes/no). "
    "Do not include any maps, images, or embedded content — text only. "
    "After the list, rank {biz_name} ({biz_url}) among all businesses in this space. "
    "You MUST include this exact line on its own: [RANK: X/Y] "
    "where X is the position and Y is total businesses (e.g., [RANK: 7/25]). "
    "Then one sentence explaining why. "
    "Keep entire response under 200 words."
)

# Platform URLs
PLATFORM_URLS = {
    "Gemini": "https://gemini.google.com",
    "ChatGPT": "https://chatgpt.com",
    "Perplexity": "https://www.perplexity.ai",
}


def slugify(text):
    """Convert text to a URL-friendly slug."""
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:60]


def build_audit_prompt(client):
    return AUDIT_PROMPT_TEMPLATE.format(**client)


def format_response_text(raw_text, platform):
    """Clean up raw AI response text for saving."""
    if not raw_text:
        return ""

    # Remove platform noise
    noise = [
        "Gemini said", "By the way,", "enable Gemini Apps Activity",
        "Sources", "ChatGPT can make mistakes", "Check important info",
        "Follow-ups", "Ask a follow-up", "+1", "+2",
        "Use two fingers", "Hold Ctrl", "© Mapbox", "© OpenStreetMap",
        "Terms", "Ask a follow",
    ]
    lines = raw_text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if any(stripped.startswith(n) or stripped == n for n in noise):
            continue
        if stripped in ("+1", "+2", "+3"):
            continue
        cleaned.append(stripped)

    if not cleaned:
        return ""

    # Detect list items and add numbering if missing
    # List items typically start with a business name followed by ":" or "–"
    numbered = []
    item_num = 0
    for line in cleaned:
        # Already numbered (e.g., "1. Business Name")
        if re.match(r'^\d+[.)]\s', line):
            numbered.append(line)
            item_num = int(re.match(r'^(\d+)', line).group(1))
            continue

        # Looks like a list item (Name: description or Name – description)
        # Only number the first few items (top 3-5)
        if (re.match(r"^[A-Z][\w\s''\-&]+[:\u2013\u2014\u2013]", line)
                and item_num < 10
                and "Google Maps" not in line.split(":")[0]
                and "Rank" not in line.split(":")[0]):
            # Check if the previous line was a list item continuation
            if "Google Maps:" in line.lower() or "appears on google" in line.lower():
                numbered.append(f"   {line}")
                continue
            item_num += 1
            numbered.append(f"#{item_num}. {line}")
        else:
            numbered.append(line)

    return "\n\n".join(numbered)


def extract_ranking(response_text, biz_name, biz_url=""):
    """
    Extract the ranking position of a business from the AI response text.
    Searches for numbered items and checks if the business name or URL appears.

    Returns:
        dict with:
          - position: int or None (e.g., 1, 2, 5, None if not in list)
          - mentioned: bool (appears anywhere in response)
          - context: str (the line/sentence where it was found)
    """
    if not response_text:
        return {"position": None, "total": None, "mentioned": False, "context": ""}

    # Normalize curly quotes and strip apostrophes for flexible matching
    def normalize(s):
        return s.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')

    def strip_apostrophes(s):
        return s.replace("'", "").replace("\u2019", "")

    text = normalize(response_text.strip())
    biz_lower = normalize(biz_name).lower()
    biz_no_apos = strip_apostrophes(biz_lower)
    # Also match partial name (e.g., "Mae's" from "Mae's Childcare")
    biz_parts = [p.lower() for p in biz_name.split() if len(p) > 2]
    url_domain = ""
    if biz_url:
        url_domain = biz_url.lower().replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")

    text_lower = text.lower()
    text_no_apos = strip_apostrophes(text_lower)
    mentioned = (biz_lower in text_lower
                 or biz_no_apos in text_no_apos
                 or (url_domain and url_domain in text_lower))

    lines = text.split("\n")
    position = None
    total = None
    context = ""

    def matches_biz(line_text):
        """Check if a line references the business."""
        ll = line_text.lower()
        ll_no_apos = strip_apostrophes(ll)
        if biz_lower in ll or biz_no_apos in ll_no_apos:
            return True
        if url_domain and url_domain in ll:
            return True
        if len(biz_parts) >= 2:
            matches = sum(1 for p in biz_parts if p in ll)
            if matches >= 2:
                return True
        return False

    # First: look for [RANK: X/Y] template format (most reliable)
    # Scan in reverse so the actual rank (at end of response) wins over
    # the example in the prompt echo (e.g., [RANK: 7/25])
    for line in reversed(lines):
        stripped = line.strip()
        # Skip lines that are clearly the prompt/example
        if "e.g." in stripped.lower() or "example" in stripped.lower() or "where X" in stripped:
            continue
        rank_tag = re.search(r'\[RANK:\s*(\d+)\s*/\s*(\d+\+?)\]', stripped)
        if rank_tag:
            position = int(rank_tag.group(1))
            total = rank_tag.group(2)
            context = stripped[:200]
            return {"position": position, "total": total, "mentioned": True, "context": context}

    # Second: look for explicit rank statement like "#18 out of 50+" or "Rank: #12"
    for line in lines:
        stripped = line.strip()
        if (not matches_biz(stripped)
                and not re.search(r'#\d+\s*(?:out of|/)', stripped)
                and not re.search(r'(?:rank|ranked|ranks|position)[:\s]*#?\d+', stripped, re.IGNORECASE)):
            continue
        rank_match = re.search(r'#(\d+)\s*(?:out of|/)\s*(?:approximately|approx\.?|about|around|roughly|~)?\s*(\d+\+?)', stripped)
        if not rank_match:
            rank_match = re.search(r'(?:rank|ranked|ranks|position)[:\s]*#?(\d+)\s*(?:out of|/)\s*(?:approximately|approx\.?|about|around|roughly|~)?\s*(\d+\+?)', stripped, re.IGNORECASE)
        if not rank_match:
            rank_match = re.search(r'(?:rank|ranked|ranks|position)[:\s]*#?(\d+)', stripped, re.IGNORECASE)
        if rank_match:
            position = int(rank_match.group(1))
            if rank_match.lastindex >= 2:
                total = rank_match.group(2)
            context = stripped[:200]
            break

    # Skip numbered list scan if we already found explicit rank
    if position is not None:
        return {"position": position, "total": total, "mentioned": True, "context": context}

    # Track current numbered position as we scan
    current_num = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # Match numbered patterns at start of line
        num_match = re.match(r'^(?:#?\s*)?(\d+)[.):\s]', stripped)
        if not num_match:
            num_match = re.match(r'^(?:Number|No\.?)\s*(\d+)', stripped, re.IGNORECASE)

        if num_match:
            current_num = int(num_match.group(1))
            # Check this line and the next few lines for the business name
            chunk = " ".join(lines[i:i+4])
            if matches_biz(chunk):
                position = current_num
                context = stripped[:200]
                break
        elif current_num is not None and matches_biz(stripped):
            # Business name found in continuation of a numbered item
            position = current_num
            context = stripped[:200]
            break

    # If not found in numbered list, check if mentioned anywhere
    if position is None and mentioned:
        for line in lines:
            ll = normalize(line).lower()
            ll_na = strip_apostrophes(ll)
            if biz_lower in ll or biz_no_apos in ll_na or (url_domain and url_domain in ll):
                context = line.strip()[:200]
                break

    return {
        "position": position,
        "total": total,
        "mentioned": mentioned,
        "context": context,
    }


CSV_COLUMNS = [
    "timestamp", "client_id", "biz_name", "campaign_id", "campaign_name",
    "keyword", "platform", "mode", "device", "status", "duration_s",
    "rank_position", "rank_total", "mentioned", "rank_context",
    "screenshot", "response_text", "error",
    "proxy_ip", "proxy_city", "proxy_region", "proxy_zip",
]

_csv_lock = threading.Lock()


def _ensure_csv_header():
    """Create CSV with header if it doesn't exist."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_COLUMNS)


def log_entry(client, keyword, platform, mode, device, status, screenshot_path, text_path,
              error=None, proxy_info=None, duration_s=None, ranking=None):
    """Append one row to the audit CSV log."""
    ranking = ranking or {}
    proxy_info = proxy_info or {}

    row = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "client_id": client.get("id", 0),
        "biz_name": client.get("biz_name", ""),
        "campaign_id": client.get("campaign_id", "") or client.get("aeo_plan_id", ""),
        "campaign_name": client.get("campaign_name", "") or client.get("plan_name", ""),
        "keyword": keyword,
        "platform": platform,
        "mode": mode,
        "device": device[:40],
        "status": status,
        "duration_s": duration_s or "",
        "rank_position": ranking.get("position", ""),
        "rank_total": ranking.get("total", ""),
        "mentioned": "yes" if ranking.get("mentioned") else "",
        "rank_context": (ranking.get("context") or "")[:100],
        "screenshot": screenshot_path,
        "response_text": text_path,
        "error": (error or "")[:200],
        "proxy_ip": proxy_info.get("ip", ""),
        "proxy_city": proxy_info.get("ip_city", ""),
        "proxy_region": proxy_info.get("ip_region", ""),
        "proxy_zip": proxy_info.get("ip_zip", ""),
    }

    with _csv_lock:
        _ensure_csv_header()
        with open(LOG_FILE, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writerow(row)

    return row


# ── ADB Imports ──────────────────────────────────────────────────────────────

from flows_adb import (
    adb, clear_chrome, dismiss_chrome_fre, navigate_to_url as adb_navigate,
    type_text, find_and_tap, wait_for_generation as adb_wait_gen,
    tap, dump_ui, hide_keyboard, get_screen_size as adb_screen_size,
    wait_for_page_ready, keep_screen_on,
)


# ── Pre-Screenshot Helpers ───────────────────────────────────────────────────

def _cleanup_before_screenshot(serial, cdp_port=9222, cleanup_js="", use_css_font=False):
    """Run cleanup JS and set zoom via CDP right before screenshot."""
    try:
        from screenshot import cdp_connect, cdp_disconnect, cdp_eval
        import json as _json
        ws = cdp_connect(serial, local_port=cdp_port)
        if ws:
            if cleanup_js:
                cdp_eval(ws, cleanup_js)
            if use_css_font:
                # CSS font resize — for platforms where setPageScaleFactor doesn't work
                cdp_eval(ws, """
                    document.querySelectorAll('*').forEach(function(el) {
                        var style = window.getComputedStyle(el);
                        var size = parseFloat(style.fontSize);
                        if (size > 12) {
                            el.style.fontSize = (size * 0.75) + 'px';
                            el.style.lineHeight = '1.3';
                        }
                    });
                """)
            else:
                cdp_eval(ws, "document.body.style.zoom = '0.75'")
            cdp_disconnect(serial, ws, local_port=cdp_port)
            time.sleep(1)
    except Exception:
        pass


# ── Banner Dismissal ─────────────────────────────────────────────────────────

def dismiss_gemini_banner_adb(serial):
    """Dismiss Gemini 'Chat in app' banner via ADB."""
    xml = dump_ui(serial)
    if "Chat with Gemini" in xml or "Try app" in xml:
        if find_and_tap(serial, content_desc="Close"):
            time.sleep(1)
            return
        if find_and_tap(serial, text="Close"):
            time.sleep(1)
            return
        w, h = adb_screen_size(serial)
        tap(serial, 30, int(h * 0.13))
        time.sleep(1)


def dismiss_gemini_banner_appium(serial):
    """Dismiss Gemini 'Chat in app' banner via ADB tap (works in any context)."""
    w, h = get_screen_size(serial)
    x_pos = int(w * 0.09)
    y_pos = int(h * 0.23)
    subprocess.run(
        ["adb", "-s", serial, "shell", "input", "tap", str(x_pos), str(y_pos)],
        capture_output=True, timeout=5,
    )
    time.sleep(1)
    subprocess.run(
        ["adb", "-s", serial, "shell", "input", "tap", str(x_pos), str(y_pos)],
        capture_output=True, timeout=5,
    )
    time.sleep(1)


def dismiss_perplexity_comet_adb(serial):
    """Dismiss Perplexity Comet modal via Close button."""
    from flows_adb import press_back
    for _ in range(3):
        time.sleep(2)
        xml = dump_ui(serial)
        if "Comet" in xml or "Install Comet" in xml:
            if find_and_tap(serial, content_desc="Close"):
                print("    Dismissed Comet modal")
                time.sleep(1)
                continue
            press_back(serial)
            time.sleep(1)
        else:
            break


# ── File Naming ──────────────────────────────────────────────────────────────

def make_paths(client, keyword, platform, timestamp):
    """Generate file paths for screenshot and text."""
    client_id = client.get("id", 0)
    slug = slugify(keyword)
    basename = f"{client_id}_{slug}_{timestamp}"

    ss_dir = os.path.join(OUTPUT_DIR, platform)
    text_dir = os.path.join(OUTPUT_DIR, "text")
    os.makedirs(ss_dir, exist_ok=True)
    os.makedirs(text_dir, exist_ok=True)

    ss_path = os.path.join(ss_dir, f"{basename}.png")
    text_path = os.path.join(text_dir, f"{basename}_{platform}.txt")
    return ss_path, text_path


# ── ADB Audit Flows ─────────────────────────────────────────────────────────

def _dismiss_gemini_popups(serial):
    """Dismiss Chrome FRE + Gemini-specific popups (mic permission, banner)."""
    dismiss_chrome_fre(serial)
    time.sleep(3)
    find_and_tap(serial, text="No thanks")
    time.sleep(1)
    # Mic permission popup (Samsung shows this on Gemini)
    find_and_tap(serial, text="Never allow")
    find_and_tap(serial, text="Block")
    time.sleep(1)
    dismiss_gemini_banner_adb(serial)


def audit_gemini_adb(serial, client, keyword, prompt, cdp_port=9222, is_first=True):
    """Run Gemini audit via ADB."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    ss_path, text_path = make_paths(client, keyword, "Gemini", timestamp)

    if is_first:
        clear_chrome(serial)
    adb(serial, "shell", "am", "force-stop", "com.android.chrome")
    time.sleep(1)

    adb(serial, "shell", "am", "start", "--activity-clear-task",
        "-a", "android.intent.action.VIEW",
        "-d", "https://gemini.google.com", "com.android.chrome")

    if is_first:
        time.sleep(3)
        _dismiss_gemini_popups(serial)

    wait_for_page_ready(serial, platform="gemini")

    if is_first:
        # Dismiss Gemini-specific popups after page loads
        find_and_tap(serial, text="No thanks")
        time.sleep(1)
        dismiss_gemini_banner_adb(serial)

    # Type + send — tap left side to avoid mic icon
    w, h = adb_screen_size(serial)
    if not find_and_tap(serial, text="Ask Gemini"):
        tap(serial, int(w * 0.3), int(h * 0.75))
    time.sleep(1)
    # Dismiss mic permission popup if it appeared
    find_and_tap(serial, text="Never allow")
    time.sleep(0.5)
    if not find_and_tap(serial, text="Ask Gemini"):
        tap(serial, int(w * 0.3), int(h * 0.75))
    time.sleep(1)
    type_text(serial, prompt)
    time.sleep(1)
    if not find_and_tap(serial, content_desc="Send message"):
        find_and_tap(serial, text="Send") or tap(serial, w - 50, int(h * 0.85))
    time.sleep(2)

    adb_wait_gen(serial)
    time.sleep(3)
    dismiss_gemini_banner_adb(serial)
    time.sleep(1)

    # Screenshot — scroll first, then remove banner + zoom right before capture
    scroll_response_to_top(serial, "Gemini", local_port=cdp_port)
    _cleanup_before_screenshot(serial, cdp_port, """
        document.querySelectorAll('div, section').forEach(function(el) {
            var text = el.textContent || '';
            if ((text.indexOf('Chat with Gemini in an app') > -1 || text.indexOf('Try app') > -1)
                && el.offsetHeight < 200) { el.remove(); }
        });
    """)
    take_screenshot(serial, output_path=ss_path)

    # Text
    response = extract_response_text(serial, "Gemini", local_port=cdp_port)
    with open(text_path, "w") as f:
        f.write(response)

    return ss_path, text_path, timestamp


def audit_chatgpt_adb(serial, client, keyword, prompt, cdp_port=9222, is_first=True):
    """Run ChatGPT audit via ADB."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    ss_path, text_path = make_paths(client, keyword, "ChatGPT", timestamp)

    if is_first:
        clear_chrome(serial)
        time.sleep(1)

    adb(serial, "shell", "am", "force-stop", "com.android.chrome")
    time.sleep(1)

    adb(serial, "shell", "am", "start", "--activity-clear-task",
        "-a", "android.intent.action.VIEW",
        "-d", "https://chatgpt.com", "com.android.chrome")

    if is_first:
        dismiss_chrome_fre(serial)

    wait_for_page_ready(serial, platform="chatgpt")
    time.sleep(3)

    # Check for login redirect — if auth.openai.com, go back to chatgpt.com
    # Check twice with a delay to catch delayed redirects
    for login_check in range(2):
        xml = dump_ui(serial)
        if "Log in or sign up" in xml or "auth.openai" in xml or "Email address" in xml or "Continue with Google" in xml:
            print("    ChatGPT login redirect detected — re-navigating...")
            adb(serial, "shell", "am", "force-stop", "com.android.chrome")
            time.sleep(2)
            adb(serial, "shell", "am", "start", "--activity-clear-task",
                "-a", "android.intent.action.VIEW",
                "-d", "https://chatgpt.com", "com.android.chrome")
            wait_for_page_ready(serial, platform="chatgpt")
            time.sleep(3)
            break
        if login_check == 0:
            time.sleep(3)

    # Type + send
    w, h = adb_screen_size(serial)
    if not find_and_tap(serial, resource_id="prompt-textarea"):
        if not find_and_tap(serial, text="Ask anything"):
            tap(serial, w // 2, int(h * 0.85))
    time.sleep(1)
    type_text(serial, prompt)
    time.sleep(1)
    if not find_and_tap(serial, resource_id="composer-submit-button"):
        find_and_tap(serial, content_desc="Send prompt") or find_and_tap(serial, text="Send")
    time.sleep(2)

    adb_wait_gen(serial)
    time.sleep(3)

    # Dismiss ChatGPT Terms popup via ADB (X button) before screenshot
    find_and_tap(serial, content_desc="Close") or find_and_tap(serial, content_desc="Dismiss")
    time.sleep(1)

    # Dismiss popup + resize font, then scroll to top, then screenshot
    _cleanup_before_screenshot(serial, cdp_port, """
        document.querySelectorAll('button').forEach(function(btn) {
            var svg = btn.querySelector('svg');
            var parent = btn.closest('div');
            if (svg && parent && parent.textContent.indexOf('Privacy Policy') > -1) btn.click();
            var label = btn.getAttribute('aria-label') || '';
            if (label === 'Close' || label === 'Dismiss') btn.click();
        });
        document.querySelectorAll('div').forEach(function(el) {
            var text = el.textContent || '';
            if (text.indexOf('Privacy Policy') > -1 && text.indexOf('Don\\'t share') > -1
                && el.offsetHeight < 300) { el.remove(); }
        });
        // Remove maps/iframes that take up screen space
        document.querySelectorAll('iframe, .mapboxgl-map, [class*="map"], [data-testid*="map"]').forEach(function(el) {
            el.remove();
        });
        // Remove map containers (div with mapbox/leaflet canvas)
        document.querySelectorAll('canvas').forEach(function(el) {
            var parent = el.closest('div');
            if (parent && parent.offsetHeight > 100 && parent.offsetHeight < 500) {
                parent.remove();
            }
        });
    """, use_css_font=True)
    # Scroll AFTER font resize so position is correct
    scroll_response_to_top(serial, "ChatGPT", local_port=cdp_port)
    time.sleep(1)
    take_screenshot(serial, output_path=ss_path)

    # Text
    response = extract_response_text(serial, "ChatGPT", local_port=cdp_port)
    with open(text_path, "w") as f:
        f.write(response)

    return ss_path, text_path, timestamp


def audit_perplexity_adb(serial, client, keyword, prompt, cdp_port=9222, is_first=True):
    """Run Perplexity audit via ADB."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    ss_path, text_path = make_paths(client, keyword, "Perplexity", timestamp)

    if is_first:
        clear_chrome(serial)
        time.sleep(1)

    adb(serial, "shell", "am", "force-stop", "com.android.chrome")
    time.sleep(1)

    adb(serial, "shell", "am", "start", "--activity-clear-task",
        "-a", "android.intent.action.VIEW",
        "-d", "https://www.perplexity.ai", "com.android.chrome")

    if is_first:
        dismiss_chrome_fre(serial)

    wait_for_page_ready(serial, platform="perplexity")
    time.sleep(2)
    dismiss_perplexity_comet_adb(serial)

    # Type + send — try CDP focus first, then ADB tap fallback
    w, h = adb_screen_size(serial)
    cdp_focused = False
    try:
        from screenshot import cdp_connect, cdp_eval, cdp_disconnect
        ws = cdp_connect(serial, local_port=cdp_port)
        if ws:
            cdp_eval(ws, """
                var input = document.querySelector('textarea[placeholder]')
                    || document.querySelector('[contenteditable]')
                    || document.querySelector('input[type="text"]');
                if (input) { input.focus(); input.click(); }
            """)
            cdp_disconnect(serial, ws, local_port=cdp_port)
            cdp_focused = True
            print("    Input focused via CDP")
            time.sleep(1)
    except Exception:
        pass

    if not cdp_focused:
        input_found = (
            find_and_tap(serial, resource_id="ask-input")
            or find_and_tap(serial, text="Ask anything")
            or find_and_tap(serial, text="Type @")
            or find_and_tap(serial, text="Type /")
        )
        if not input_found:
            tap(serial, w // 2, int(h * 0.68))
        time.sleep(1)

    type_text(serial, prompt)
    time.sleep(1)

    # Submit via CDP (more reliable than ADB tap which can hit mic button)
    cdp_submitted = False
    try:
        from screenshot import cdp_connect, cdp_eval, cdp_disconnect
        ws = cdp_connect(serial, local_port=cdp_port)
        if ws:
            result = cdp_eval(ws, """
                var btn = document.querySelector('button[aria-label="Submit"]')
                    || document.querySelector('button[type="submit"]')
                    || document.querySelector('button svg[data-icon="arrow-right"]');
                if (btn) { if (btn.tagName !== 'BUTTON') btn = btn.closest('button'); btn.click(); 'clicked'; }
                else { 'not found'; }
            """)
            cdp_disconnect(serial, ws, local_port=cdp_port)
            if result and "clicked" in str(result):
                cdp_submitted = True
                print("    Submitted via CDP")
            time.sleep(2)
    except Exception:
        pass

    if not cdp_submitted:
        # Fallback: hide keyboard then find Submit via ADB
        from flows_adb import hide_keyboard_and_submit
        hide_keyboard_and_submit(serial)
    time.sleep(3)

    # Verify submit worked — if no generation started, retry
    from flows_adb import hide_keyboard_and_submit
    xml = dump_ui(serial)
    has_stop = any(p in xml for p in ["Stop streaming", "Stop generating", "Stop response"])
    if not has_stop:
        print("    Submit may not have worked — retrying...")
        hide_keyboard_and_submit(serial)
        time.sleep(2)
        xml = dump_ui(serial)
        has_stop = any(p in xml for p in ["Stop streaming", "Stop generating", "Stop response"])
        if not has_stop:
            print("    Pressing Enter as fallback...")
            adb(serial, "shell", "input", "keyevent", "66", timeout=5)
            time.sleep(2)

    adb_wait_gen(serial)
    time.sleep(3)

    # Screenshot — scroll first, then remove banner + zoom right before capture
    scroll_response_to_top(serial, "Perplexity", local_port=cdp_port)
    _cleanup_before_screenshot(serial, cdp_port, """
        document.querySelectorAll('div, a, button').forEach(function(el) {
            if (el.textContent.trim() === 'Open in App') el.remove();
        });
    """)
    take_screenshot(serial, output_path=ss_path)

    # Text
    response = extract_response_text(serial, "Perplexity", local_port=cdp_port)
    with open(text_path, "w") as f:
        f.write(response)

    return ss_path, text_path, timestamp


# ── Appium Audit Flows ───────────────────────────────────────────────────────

def _create_appium_driver(serial, port):
    """Create Appium driver for audit."""
    from appium import webdriver
    from appium.options.android.uiautomator2.base import UiAutomator2Options
    from flows import clear_chrome as appium_clear

    appium_clear(serial)
    time.sleep(1)

    opts = UiAutomator2Options()
    opts.platform_name = "Android"
    opts.device_name = serial
    opts.udid = serial
    opts.automation_name = "UiAutomator2"
    opts.no_reset = False
    opts.new_command_timeout = 300
    opts.orientation = "PORTRAIT"
    opts.app_package = "com.android.chrome"
    opts.app_activity = "com.google.android.apps.chrome.Main"
    opts.set_capability("appium:chromeOptions", {"args": []})
    opts.set_capability("appium:chromedriverAutodownload", True)

    driver = webdriver.Remote(f"http://127.0.0.1:{port}/wd/hub", options=opts)
    driver.implicitly_wait(5)
    return driver


def _appium_send_and_wait(driver, serial, platform, prompt):
    """Common Appium flow: FRE → navigate → type → send → wait."""
    from appium.webdriver.common.appiumby import AppiumBy
    from flows import (
        dismiss_first_run_dialogs, navigate_to_url as appium_navigate,
        switch_to_webview, switch_to_native, webview_find, webview_set_text,
        wait_for_generation as appium_wait_gen, tap_optional,
    )

    dismiss_first_run_dialogs(driver)

    url = PLATFORM_URLS[platform].replace("https://", "")
    appium_navigate(driver, url)

    # Platform-specific popups
    if platform == "Gemini":
        tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                     'new UiSelector().text("No thanks")', timeout=3)
    elif platform == "Perplexity":
        time.sleep(3)
        tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                     'new UiSelector().text("Close")', timeout=4)
        tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                     'new UiSelector().text("Close")', timeout=3)
        tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                     'new UiSelector().text("Maybe later")', timeout=2)

    time.sleep(3)
    if not switch_to_webview(driver):
        raise RuntimeError("WebView context not available")

    # Platform-specific JS dismissals
    if platform == "Gemini":
        driver.execute_script("""
            document.querySelectorAll('button[aria-label="Close"], button[aria-label="Dismiss"]')
                .forEach(btn => { if (btn.offsetParent !== null) btn.click(); });
        """)
    elif platform == "Perplexity":
        driver.execute_script("""
            document.querySelectorAll('button[aria-label="Close"], button[aria-label="Dismiss"]').forEach(btn => {
                if (btn.offsetParent !== null) btn.click();
            });
        """)
    time.sleep(1)

    # Find input
    input_selectors = {
        "Gemini": ["div[contenteditable='true']", "textarea", "[role='textbox']"],
        "ChatGPT": ["#prompt-textarea", "textarea", "div[contenteditable='true']"],
        "Perplexity": ["#ask-input", "div[contenteditable='true']", "textarea"],
    }
    input_el = webview_find(driver, input_selectors[platform], timeout=20)
    if platform == "Perplexity":
        driver.execute_script("arguments[0].focus(); arguments[0].click();", input_el)
    else:
        input_el.click()
    time.sleep(0.3)
    webview_set_text(driver, input_el, prompt)

    # Send
    send_selectors = {
        "Gemini": ["button[aria-label='Send message']", "button[aria-label*='Send']"],
        "ChatGPT": ["#composer-submit-button", "button[aria-label='Send prompt']"],
        "Perplexity": ["button[aria-label='Submit']", "button[type='submit']"],
    }
    send_btn = webview_find(driver, send_selectors[platform], timeout=10)
    if platform == "Perplexity":
        driver.execute_script("arguments[0].disabled = false; arguments[0].click();", send_btn)
    else:
        send_btn.click()

    # Wait for generation
    appium_wait_gen(driver, platform)
    time.sleep(3)

    return driver


def audit_platform_appium(serial, client, keyword, prompt, platform, port):
    """Run audit on any platform via Appium."""
    from flows import switch_to_webview, switch_to_native, webview_find

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    ss_path, text_path = make_paths(client, keyword, platform, timestamp)

    driver = _create_appium_driver(serial, port)

    try:
        driver = _appium_send_and_wait(driver, serial, platform, prompt)

        # Dismiss Gemini banner via ADB tap
        if platform == "Gemini":
            dismiss_gemini_banner_appium(serial)

        # Make sure we're in WebView for scroll + text
        try:
            switch_to_webview(driver)
        except Exception:
            pass
        time.sleep(1)

        # Scroll response to top
        scroll_js = {
            "Gemini": """
                let el = document.querySelector('model-response, message-content, .model-response-text');
                if (!el) { let all = document.querySelectorAll('[class*="response"], [class*="model"]');
                    for (let a of all) { if (a.innerText && a.innerText.length > 50) { el = a; break; } } }
                if (el) { el.scrollIntoView({behavior: 'instant', block: 'start'}); window.scrollBy(0, -150); }
            """,
            "ChatGPT": """
                let el = document.querySelector('[data-message-author-role="assistant"], article');
                if (el) { el.scrollIntoView({behavior: 'instant', block: 'start'}); window.scrollBy(0, -150); }
            """,
            "Perplexity": """
                let el = document.querySelector('[class*="answer"], .prose, [class*="response"]');
                if (el) { el.scrollIntoView({behavior: 'instant', block: 'start'}); window.scrollBy(0, -150); }
            """,
        }
        driver.execute_script(scroll_js.get(platform, scroll_js["Gemini"]))
        time.sleep(1)

        # Extract text
        text_js = {
            "Gemini": """
                let el = document.querySelector('model-response, message-content');
                if (!el) { let all = document.querySelectorAll('[class*="response"]');
                    for (let a of all) { if (a.innerText?.length > 50) { el = a; break; } } }
                return el ? el.innerText : document.body.innerText;
            """,
            "ChatGPT": """
                let el = document.querySelector('[data-message-author-role="assistant"]');
                return el ? el.innerText : document.body.innerText;
            """,
            "Perplexity": """
                let el = document.querySelector('[class*="answer"], .prose');
                return el ? el.innerText : document.body.innerText;
            """,
        }
        response = driver.execute_script(text_js.get(platform, text_js["Gemini"]))

        switch_to_native(driver)
        time.sleep(1)

        # Screenshot
        take_screenshot(serial, output_path=ss_path)

        # Save text
        with open(text_path, "w") as f:
            f.write(response or "")

    finally:
        driver.quit()

    return ss_path, text_path, timestamp


# ── ADB Flow Dispatcher ─────────────────────────────────────────────────────

ADB_AUDIT_FLOWS = {
    "Gemini": audit_gemini_adb,
    "ChatGPT": audit_chatgpt_adb,
    "Perplexity": audit_perplexity_adb,
}


# ── Main Runner ──────────────────────────────────────────────────────────────

def run_audit(client, keyword, platform, serial, mode="adb", port=4723, cdp_port=9222,
              proxy_info=None, is_first=True):
    """
    Run a single audit: send ranking prompt, capture screenshot + text.

    Args:
        client: dict with biz_name, biz_url, city, state, id
        keyword: the keyword to audit
        platform: "Gemini", "ChatGPT", or "Perplexity"
        serial: full ADB serial
        mode: "adb" or "appium"
        port: Appium port (only for appium mode)
        cdp_port: Chrome DevTools port (unique per device in parallel)
        proxy_info: proxy connection info (passed from caller, not managed here)

    Returns:
        dict with status, screenshot, text, timestamp
    """
    prompt = build_audit_prompt({**client, "keyword": keyword})

    print(f"\n{'='*60}")
    print(f"AUDIT: {platform} ({mode.upper()}) [CDP port {cdp_port}]")
    print(f"Client: {client['biz_name']} | Keyword: {keyword}")
    print(f"Device: {serial[:40]}...")
    print(f"{'='*60}")

    start_time = time.time()

    try:
        if mode == "adb":
            flow_fn = ADB_AUDIT_FLOWS.get(platform)
            if not flow_fn:
                raise ValueError(f"Unknown platform: {platform}")
            ss_path, text_path, timestamp = flow_fn(serial, client, keyword, prompt, cdp_port=cdp_port, is_first=is_first)
        else:
            ss_path, text_path, timestamp = audit_platform_appium(
                serial, client, keyword, prompt, platform, port
            )

        duration = round(time.time() - start_time, 1)

        # Read raw text, format it, extract ranking, and rewrite
        response_text = ""
        try:
            with open(text_path) as f:
                response_text = f.read()
        except Exception:
            pass

        ranking = extract_ranking(response_text, client.get("biz_name", ""),
                                  client.get("biz_url", ""))

        entry = log_entry(client, keyword, platform, mode, serial,
                          "success", ss_path, text_path,
                          proxy_info=proxy_info, duration_s=duration,
                          ranking=ranking)

        # Rewrite text file with formatted text + ranking summary
        if response_text:
            try:
                formatted = format_response_text(response_text, platform)
                pos = ranking.get("position")
                total_s = f" out of {ranking.get('total')}" if ranking.get("total") else ""
                with open(text_path, "w") as f:
                    f.write(formatted)
                    f.write(f"\n\n--- AEO Ranking ---\n")
                    f.write(f"Business: {client.get('biz_name', '')}\n")
                    f.write(f"Keyword: {keyword}\n")
                    f.write(f"Platform: {platform}\n")
                    f.write(f"Position: {f'#{pos}{total_s}' if pos else 'Not ranked'}\n")
                    f.write(f"Mentioned: {'Yes' if ranking.get('mentioned') else 'No'}\n")
                    if ranking.get("context"):
                        f.write(f"Context: {ranking['context']}\n")
            except Exception:
                pass

        pos = ranking.get("position")
        total_str = f" out of {ranking['total']}" if ranking.get("total") else ""
        pos_str = f"#{pos}{total_str}" if pos else ("mentioned" if ranking.get("mentioned") else "not found")
        print(f"\n  Screenshot: {ss_path}")
        print(f"  Text: {text_path}")
        print(f"  Ranking: {pos_str}")
        print(f"  Status: SUCCESS ({duration}s)")
        return {"status": "success", "screenshot": ss_path, "text": text_path,
                "timestamp": timestamp, "ranking": ranking}

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        duration = round(time.time() - start_time, 1)
        print(f"\n  ERROR: {error_msg}")
        log_entry(client, keyword, platform, mode, serial,
                  "error", "", "", error=error_msg,
                  proxy_info=proxy_info, duration_s=duration)
        return {"status": "error", "error": error_msg}


# ── CLI ──────────────────────────────────────────────────────────────────────

TEST_CLIENT = {
    "id": 0,
    "biz_name": "Mae's Childcare",
    "biz_url": "https://www.maeschildcare.com",
    "city": "San Francisco",
    "state": "California",
    "keywords": ["bilingual childcare"],
    "proxy": {
        "session_duration": 30,
        "country": "us",
        "zip": "94117",
        "latitude": 37.77784,
        "longitude": -122.430147,
        "timezone": "America/Los_Angeles"
    },
}


def main():
    parser = argparse.ArgumentParser(description="AEO Ranking Audit")
    parser.add_argument("--test", action="store_true", help="Use test client (no clients.json)")
    parser.add_argument("--platform", choices=PLATFORMS, help="Single platform only")
    parser.add_argument("--clients", type=int, help="Limit to first N clients")
    parser.add_argument("--serial", help="Override device serial")
    parser.add_argument("--mode", choices=["adb", "appium"], default="adb")
    parser.add_argument("--port", type=int, default=4723, help="Appium port")
    parser.add_argument("--keyword-index", type=int, default=0,
                        help="Which keyword to audit (0 = first)")
    parser.add_argument("--all-devices", action="store_true",
                        help="Run on ALL connected devices in parallel (ADB mode)")
    parser.add_argument("--exclude", help="Exclude device serials (comma-separated substrings)")
    args = parser.parse_args()

    platforms = [args.platform] if args.platform else PLATFORMS

    # Load clients
    if args.test:
        clients = [TEST_CLIENT]
    else:
        with open("clients.json") as f:
            clients = json.load(f)
        if args.clients:
            clients = clients[:args.clients]

    # ── All-devices parallel mode ──
    if args.all_devices:
        import random

        # Discover devices
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
        devices = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.strip().split("\t")
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])

        # Exclude devices
        if args.exclude:
            excludes = [e.strip() for e in args.exclude.split(",")]
            devices = [d for d in devices if not any(ex in d for ex in excludes)]

        if not devices:
            print("No devices found.")
            return

        # Get brand info
        device_info = []
        for serial in devices:
            brand = subprocess.run(
                ["adb", "-s", serial, "shell", "getprop", "ro.product.brand"],
                capture_output=True, text=True, timeout=5
            ).stdout.strip()
            model = subprocess.run(
                ["adb", "-s", serial, "shell", "getprop", "ro.product.model"],
                capture_output=True, text=True, timeout=5
            ).stdout.strip()
            device_info.append({"serial": serial, "brand": brand, "model": model})

        # Build all keyword jobs: each keyword runs all 3 platforms (1 session)
        jobs = []
        for client in clients:
            for kw in client.get("keywords", []):
                keyword = kw["keyword"] if isinstance(kw, dict) else kw
                plats = [args.platform] if args.platform else list(PLATFORMS)
                jobs.append({"client": client, "keyword": keyword, "platforms": plats})

        # Distribute jobs round-robin across devices
        # Each device gets a queue of jobs to run sequentially
        device_queues = {i: [] for i in range(len(device_info))}
        for idx, job in enumerate(jobs):
            device_idx = idx % len(device_info)
            device_queues[device_idx].append(job)

        # Print plan
        print(f"\nAEO Ranking Audit — ALL DEVICES")
        print(f"{'='*60}")
        print(f"Devices: {len(device_info)}")
        for d in device_info:
            print(f"  {d['brand']} {d['model']} ({d['serial'][:30]}...)")
        print(f"Clients: {len(clients)}")
        print(f"Total keywords: {len(jobs)}")
        print(f"\nDistribution:")
        for dev_idx, queue in device_queues.items():
            d = device_info[dev_idx]
            print(f"  {d['brand']} {d['model']}: {len(queue)} keywords")
            for j in queue[:3]:
                print(f"    {j['client']['biz_name']} | {j['keyword'][:30]} | {','.join(j.get('platforms', []))}")
            if len(queue) > 3:
                print(f"    ... and {len(queue) - 3} more")
        print(f"{'='*60}")

        all_results = []
        results_lock = threading.Lock()

        def device_worker(dev_idx, dev, queue, cdp_port):
            """Run all assigned jobs sequentially on one device.
            Each job = 1 keyword × all 3 platforms under same proxy session.
            Proxy reconnects per keyword (new session ID = new IP)."""
            from proxy import setup_device, teardown_device

            # Keep screen on for the entire audit
            keep_screen_on(dev["serial"])

            from proxy import verify_proxy_via_chrome

            for job_idx, job in enumerate(queue):
                client = job["client"]
                proxy_config = client.get("proxy")
                if proxy_config:
                    from proxy import enrich_proxy_config
                    proxy_config = enrich_proxy_config(proxy_config, client)
                proxy_info = None

                # Setup proxy per keyword (new session ID = new IP) + preflight verify
                if proxy_config:
                    preflight_ok = False
                    for preflight_attempt in (1, 2):
                        if job_idx > 0 or preflight_attempt > 1:
                            try:
                                teardown_device(dev["serial"])
                            except Exception:
                                pass
                            time.sleep(2)

                        print(f"\n[{dev['brand']}] Setting up proxy for {client['biz_name']} | {job['keyword'][:30]}... (attempt {preflight_attempt}/2)")
                        device_setup = setup_device(dev["serial"], proxy_config)
                        proxy_info = device_setup.get("proxy", {})
                        time.sleep(3)

                        # Preflight: launch Chrome to ifconfig.me and verify the IP
                        # returned is NOT the laptop's real public IP. Catches silent
                        # VPN-drop / IP-leak where tun0 is down and wlan0 takes over.
                        verify = verify_proxy_via_chrome(dev["serial"], cdp_port=cdp_port, timeout=30)
                        if verify.get("ok"):
                            print(f"  [Preflight] OK — device IP {verify['ip']} (real IP was {verify.get('real_ip','?')})")
                            proxy_info["verified_ip"] = verify["ip"]
                            preflight_ok = True
                            break
                        print(f"  [Preflight] FAILED ({preflight_attempt}/2): {verify.get('reason','?')}")

                    if not preflight_ok:
                        print(f"[{dev['brand']}] SKIPPING keyword — proxy preflight failed after 2 attempts")
                        with results_lock:
                            all_results.append({
                                "status": "error",
                                "error": "proxy_preflight_failed",
                                "device": dev["serial"],
                                "keyword": job["keyword"],
                                "client": client.get("biz_name", ""),
                            })
                        continue

                # Run all platforms for this keyword — always clear Chrome first
                # (is_first=True) so cookies/history don't carry over between platforms.
                for plat_idx, plat in enumerate(job["platforms"]):
                    print(f"\n[{dev['brand']} {dev['model']}] {plat} — "
                          f"{client['biz_name']} | {job['keyword'][:30]}")
                    r = run_audit(
                        client=client,
                        keyword=job["keyword"],
                        platform=plat,
                        serial=dev["serial"],
                        mode="adb",
                        cdp_port=cdp_port,
                        proxy_info=proxy_info,
                        is_first=True,
                    )
                    with results_lock:
                        all_results.append(r)

            # Final teardown
            if queue:
                try:
                    teardown_device(dev["serial"])
                except Exception:
                    pass

        # Start one thread per device — each runs its queue sequentially
        threads = []
        for dev_idx, queue in device_queues.items():
            if not queue:
                continue
            cdp_port = int(os.environ.get("AUDIT_CDP_BASE", "9222")) + dev_idx
            t = threading.Thread(
                target=device_worker,
                args=(dev_idx, device_info[dev_idx], queue, cdp_port),
                daemon=True,
            )
            threads.append(t)

        for t in threads:
            t.start()
            time.sleep(2)  # stagger to avoid ADB collisions at start

        for t in threads:
            t.join()

        success = sum(1 for r in all_results if r["status"] == "success")
        failed = sum(1 for r in all_results if r["status"] == "error")
        print(f"\n{'='*60}")
        print(f"AUDIT COMPLETE: {success} passed, {failed} failed")
        print(f"Devices: {len(device_info)} | Keywords: {len(jobs)}")
        print(f"Results in: {OUTPUT_DIR}/")
        print(f"{'='*60}")
        return

    # ── Single-device mode ──
    if not args.serial:
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines()[1:]:
            parts = line.strip().split("\t")
            if len(parts) >= 2 and parts[1] == "device":
                args.serial = parts[0]
                break
        if not args.serial:
            print("No device found. Specify --serial")
            return

    print(f"\nAEO Ranking Audit")
    print(f"Mode: {args.mode.upper()}")
    print(f"Device: {args.serial}")
    print(f"Platforms: {', '.join(platforms)}")
    print(f"Clients: {len(clients)}")

    from proxy import setup_device, teardown_device

    results = []
    is_first_job = True
    is_first_platform = True

    for client in clients:
        raw_kws = client.get("keywords", [])
        if not raw_kws:
            continue

        proxy_config = client.get("proxy")
        if proxy_config:
            from proxy import enrich_proxy_config
            proxy_config = enrich_proxy_config(proxy_config, client)

        kw_idx = min(args.keyword_index, len(raw_kws) - 1)
        kw_entry = raw_kws[kw_idx]
        keyword = kw_entry["keyword"] if isinstance(kw_entry, dict) else kw_entry
        proxy_info = None

        # Setup proxy per keyword (new session ID = new IP)
        if proxy_config:
            if not is_first_job:
                try:
                    teardown_device(args.serial)
                except Exception:
                    pass
                time.sleep(2)

            print(f"\nSetting up proxy for {client['biz_name']} | {keyword[:30]}...")
            device_setup = setup_device(args.serial, proxy_config)
            proxy_info = device_setup.get("proxy", {})
            time.sleep(3)
            is_first_job = False

        # Run all platforms for this keyword under same proxy
        for platform in platforms:
            result = run_audit(
                client=client,
                keyword=keyword,
                platform=platform,
                serial=args.serial,
                mode=args.mode,
                port=args.port,
                proxy_info=proxy_info,
                is_first=is_first_platform,
            )
            results.append(result)
            is_first_platform = False

    # Final teardown
    if not is_first_job:
        try:
            teardown_device(args.serial)
        except Exception:
            pass

    success = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if r["status"] == "error")
    print(f"\n{'='*60}")
    print(f"AUDIT COMPLETE: {success} passed, {failed} failed")
    print(f"Results in: {OUTPUT_DIR}/")
    print(f"Log: {LOG_FILE}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
