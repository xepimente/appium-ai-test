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
import json
import os
import re
import subprocess
import time

from screenshot import take_screenshot, scroll_response_to_top, extract_response_text, get_screen_size

# ── Config ────────────────────────────────────────────────────────────────────

OUTPUT_DIR = "audit_results"
LOG_FILE = os.path.join(OUTPUT_DIR, "audit_log.json")
PLATFORMS = ["Gemini", "ChatGPT", "Perplexity"]

# Concise prompt — forces a short response that fits in 1 mobile screen
AUDIT_PROMPT_TEMPLATE = (
    "Top 3 businesses for {keyword} in {city}, {state}. "
    "Format: numbered list, each line: name - one short reason - Google Maps (yes/no). "
    "No intro, no bullet points, no extra detail. "
    "After the list, one sentence: is {biz_name} ({biz_url}) a leader? "
    "Keep entire response under 100 words."
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


def load_log():
    """Load the audit log."""
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE) as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
        except (json.JSONDecodeError, ValueError):
            pass
    return []


def save_log(entries):
    """Save the audit log."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(LOG_FILE, "w") as f:
        json.dump(entries, f, indent=2)


def log_entry(client, keyword, platform, mode, device, status, screenshot_path, text_path, error=None):
    """Add an entry to the audit log."""
    entries = load_log()
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "client_id": client.get("id", 0),
        "biz_name": client.get("biz_name", ""),
        "keyword": keyword,
        "platform": platform,
        "mode": mode,
        "device": device,
        "status": status,
        "screenshot": screenshot_path,
        "response_text": text_path,
    }
    if error:
        entry["error"] = error
    entries.append(entry)
    save_log(entries)
    return entry


# ── ADB Imports ──────────────────────────────────────────────────────────────

from flows_adb import (
    adb, clear_chrome, dismiss_chrome_fre, navigate_to_url as adb_navigate,
    type_text, find_and_tap, wait_for_generation as adb_wait_gen,
    tap, dump_ui, hide_keyboard, get_screen_size as adb_screen_size,
)


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
    """Dismiss Perplexity Comet modal via coordinate tap."""
    w, h = adb_screen_size(serial)
    x_pos = int(w * 0.943)
    y_pos = int(h * 0.134)
    for _ in range(2):
        time.sleep(2)
        tap(serial, x_pos, y_pos)
        time.sleep(1)


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


def audit_gemini_adb(serial, client, keyword, prompt, cdp_port=9222):
    """Run Gemini audit via ADB."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    ss_path, text_path = make_paths(client, keyword, "Gemini", timestamp)

    clear_chrome(serial)
    time.sleep(1)
    adb(serial, "shell", "am", "start", "-a", "android.intent.action.VIEW",
        "-d", "https://gemini.google.com", "com.android.chrome")
    time.sleep(5)

    _dismiss_gemini_popups(serial)

    # Type + send
    w, h = adb_screen_size(serial)
    tap(serial, w // 2, int(h * 0.85))
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

    # Screenshot
    scroll_response_to_top(serial, "Gemini", local_port=cdp_port)
    time.sleep(1)
    take_screenshot(serial, output_path=ss_path)

    # Text
    response = extract_response_text(serial, "Gemini", local_port=cdp_port)
    with open(text_path, "w") as f:
        f.write(response)

    return ss_path, text_path, timestamp


def audit_chatgpt_adb(serial, client, keyword, prompt, cdp_port=9222):
    """Run ChatGPT audit via ADB."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    ss_path, text_path = make_paths(client, keyword, "ChatGPT", timestamp)

    clear_chrome(serial)
    time.sleep(1)
    adb(serial, "shell", "am", "start", "-a", "android.intent.action.VIEW",
        "-d", "https://chatgpt.com", "com.android.chrome")
    time.sleep(5)

    dismiss_chrome_fre(serial)
    time.sleep(8)

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

    # Screenshot
    scroll_response_to_top(serial, "ChatGPT", local_port=cdp_port)
    time.sleep(1)
    take_screenshot(serial, output_path=ss_path)

    # Text
    response = extract_response_text(serial, "ChatGPT", local_port=cdp_port)
    with open(text_path, "w") as f:
        f.write(response)

    return ss_path, text_path, timestamp


def audit_perplexity_adb(serial, client, keyword, prompt, cdp_port=9222):
    """Run Perplexity audit via ADB."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    ss_path, text_path = make_paths(client, keyword, "Perplexity", timestamp)

    clear_chrome(serial)
    time.sleep(1)
    adb(serial, "shell", "am", "start", "-a", "android.intent.action.VIEW",
        "-d", "https://www.perplexity.ai", "com.android.chrome")
    time.sleep(5)

    dismiss_chrome_fre(serial)
    time.sleep(5)
    dismiss_perplexity_comet_adb(serial)

    # Type + send
    if not find_and_tap(serial, resource_id="ask-input"):
        if not find_and_tap(serial, text="Ask anything"):
            w, h = adb_screen_size(serial)
            tap(serial, w // 2, int(h * 0.6))
    time.sleep(1)
    type_text(serial, prompt)
    time.sleep(1)

    # Perplexity needs keyboard hide + submit poll
    from flows_adb import hide_keyboard_and_submit
    hide_keyboard_and_submit(serial)
    time.sleep(2)

    adb_wait_gen(serial)
    time.sleep(3)

    # Screenshot
    scroll_response_to_top(serial, "Perplexity", local_port=cdp_port)
    time.sleep(1)
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

def run_audit(client, keyword, platform, serial, mode="adb", port=4723, cdp_port=9222):
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

    Returns:
        dict with status, screenshot, text, timestamp
    """
    prompt = build_audit_prompt({**client, "keyword": keyword})

    print(f"\n{'='*60}")
    print(f"AUDIT: {platform} ({mode.upper()}) [CDP port {cdp_port}]")
    print(f"Client: {client['biz_name']} | Keyword: {keyword}")
    print(f"Device: {serial[:40]}...")
    print(f"{'='*60}")

    try:
        if mode == "adb":
            flow_fn = ADB_AUDIT_FLOWS.get(platform)
            if not flow_fn:
                raise ValueError(f"Unknown platform: {platform}")
            ss_path, text_path, timestamp = flow_fn(serial, client, keyword, prompt, cdp_port=cdp_port)
        else:
            ss_path, text_path, timestamp = audit_platform_appium(
                serial, client, keyword, prompt, platform, port
            )

        entry = log_entry(client, keyword, platform, mode, serial,
                          "success", ss_path, text_path)
        print(f"\n  Screenshot: {ss_path}")
        print(f"  Text: {text_path}")
        print(f"  Status: SUCCESS")
        return {"status": "success", "screenshot": ss_path, "text": text_path,
                "timestamp": timestamp}

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        print(f"\n  ERROR: {error_msg}")
        log_entry(client, keyword, platform, mode, serial,
                  "error", "", "", error=error_msg)
        return {"status": "error", "error": error_msg}


# ── CLI ──────────────────────────────────────────────────────────────────────

TEST_CLIENT = {
    "id": 0,
    "biz_name": "Mae's Childcare",
    "biz_url": "https://www.maeschildcare.com",
    "city": "San Francisco",
    "state": "California",
    "keywords": ["bilingual childcare"],
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
        import threading
        import random

        # Discover devices
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
        devices = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.strip().split()
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

        # Build all keyword jobs: (client, keyword, random_platform)
        jobs = []
        for client in clients:
            for kw in client.get("keywords", []):
                keyword = kw["keyword"] if isinstance(kw, dict) else kw
                platform = args.platform if args.platform else random.choice(PLATFORMS)
                jobs.append({"client": client, "keyword": keyword, "platform": platform})

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
                print(f"    {j['client']['biz_name']} | {j['keyword'][:30]} | {j['platform']}")
            if len(queue) > 3:
                print(f"    ... and {len(queue) - 3} more")
        print(f"{'='*60}")

        all_results = []
        results_lock = threading.Lock()

        def device_worker(dev_idx, dev, queue, cdp_port):
            """Run all assigned jobs sequentially on one device."""
            for job in queue:
                print(f"\n[{dev['brand']} {dev['model']}] {job['platform']} — "
                      f"{job['client']['biz_name']} | {job['keyword'][:30]}")
                r = run_audit(
                    client=job["client"],
                    keyword=job["keyword"],
                    platform=job["platform"],
                    serial=dev["serial"],
                    mode="adb",
                    cdp_port=cdp_port,
                )
                with results_lock:
                    all_results.append(r)

        # Start one thread per device — each runs its queue sequentially
        threads = []
        for dev_idx, queue in device_queues.items():
            if not queue:
                continue
            cdp_port = 9222 + dev_idx
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
            parts = line.strip().split()
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

    results = []
    for client in clients:
        raw_kws = client.get("keywords", [])
        if not raw_kws:
            continue

        kw_idx = min(args.keyword_index, len(raw_kws) - 1)
        kw_entry = raw_kws[kw_idx]
        keyword = kw_entry["keyword"] if isinstance(kw_entry, dict) else kw_entry

        for platform in platforms:
            result = run_audit(
                client=client,
                keyword=keyword,
                platform=platform,
                serial=args.serial,
                mode=args.mode,
                port=args.port,
            )
            results.append(result)

    success = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if r["status"] == "error")
    print(f"\n{'='*60}")
    print(f"AUDIT COMPLETE: {success} passed, {failed} failed")
    print(f"Results in: {OUTPUT_DIR}/")
    print(f"Log: {LOG_FILE}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
