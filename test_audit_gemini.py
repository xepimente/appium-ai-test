"""
Test Audit Flow — Gemini
-------------------------
Tests the ranking audit flow on Gemini:
1. Clear Chrome + navigate to Gemini
2. Send concise audit prompt (designed to get a short response)
3. Wait for generation
4. Scroll down so only AI response is visible (no prompt on screen)
5. Take one single screenshot
6. Save locally

Usage:
  python3 test_audit_gemini.py --mode adb      # test on Infinix (ADB-only)
  python3 test_audit_gemini.py --mode appium    # test on Samsung (Appium)
"""

import argparse
import json
import os
import subprocess
import time

from screenshot import take_screenshot, scroll_response_to_top, extract_response_text, adb_cmd, get_screen_size

# ── Config ────────────────────────────────────────────────────────────────────

SAMSUNG_SERIAL = "adb-R83L103VCVH-uvv2pp._adb-tls-connect._tcp"
INFINIX_SERIAL = "adb-149145555W001028-XsQtPA._adb-tls-connect._tcp"

OUTPUT_DIR = "audit_results"

# Test client data
TEST_CLIENT = {
    "biz_name": "Mae's Childcare",
    "biz_url": "https://www.maeschildcare.com",
    "city": "San Francisco",
    "state": "California",
    "keyword": "bilingual childcare",
}

# Concise audit prompt — forces a short ranked list that fits in 1 screen
AUDIT_PROMPT_TEMPLATE = (
    "Top 3 most recommended businesses for {keyword} in {city}, {state}. "
    "For each: rank number, business name, one reason, Google Maps presence (yes/no). "
    "Is {biz_name} ({biz_url}) a leader in this category? "
    "Reply in a short numbered list only, no extra explanation."
)


def build_audit_prompt(client):
    return AUDIT_PROMPT_TEMPLATE.format(**client)


# ── ADB Helpers ──────────────────────────────────────────────────────────────

from flows_adb import (
    adb, clear_chrome, dismiss_chrome_fre, navigate_to_url,
    type_text, find_and_tap, wait_for_generation,
    tap, dump_ui, hide_keyboard,
)


def dismiss_gemini_banner(serial):
    """Dismiss the 'Chat with Gemini in an app' banner."""
    xml = dump_ui(serial)
    if "Chat with Gemini" in xml or "Try app" in xml:
        if find_and_tap(serial, content_desc="Close"):
            time.sleep(1)
            return
        if find_and_tap(serial, text="Close"):
            time.sleep(1)
            return
        w, h = get_screen_size(serial)
        tap(serial, 30, int(h * 0.13))
        time.sleep(1)


# ── ADB Audit Flow ───────────────────────────────────────────────────────────

def run_audit_adb(serial, client, output_dir):
    prompt = build_audit_prompt(client)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    save_dir = os.path.join(output_dir, "Gemini", timestamp)
    os.makedirs(save_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"GEMINI AUDIT (ADB) — {client['biz_name']}")
    print(f"Keyword: {client['keyword']}")
    print(f"Prompt: {prompt[:80]}...")
    print(f"Device: {serial}")
    print(f"{'='*60}")

    # 1. Clear Chrome + launch
    print("\n[1/7] Clearing Chrome...")
    clear_chrome(serial)
    time.sleep(1)

    # Launch Chrome directly to Gemini
    print("[2/7] Launching Chrome to Gemini...")
    adb(serial, "shell", "am", "start", "-a", "android.intent.action.VIEW",
        "-d", "https://gemini.google.com", "com.android.chrome")
    time.sleep(5)

    # 2. Dismiss FRE
    print("[3/7] Dismissing FRE...")
    dismiss_chrome_fre(serial)

    # Wait for Gemini to load after FRE
    time.sleep(5)
    find_and_tap(serial, text="No thanks")
    time.sleep(1)
    dismiss_gemini_banner(serial)

    # 4. Type prompt
    print("[4/7] Typing audit prompt...")
    w, h = get_screen_size(serial)
    input_y = int(h * 0.85)
    tap(serial, w // 2, input_y)
    time.sleep(1)
    type_text(serial, prompt)
    time.sleep(1)

    # 5. Send
    print("[5/7] Sending...")
    if not find_and_tap(serial, content_desc="Send message"):
        find_and_tap(serial, text="Send") or tap(serial, w - 50, input_y)
    time.sleep(2)

    # 6. Wait for generation
    print("[6/7] Waiting for AI response...")
    wait_for_generation(serial)
    time.sleep(3)

    # Dismiss banner if it reappeared
    dismiss_gemini_banner(serial)
    time.sleep(1)

    # 7. Scroll response to top via CDP, then screenshot
    print("[7/7] Positioning response + screenshot...")
    scroll_response_to_top(serial, "Gemini")
    time.sleep(1)

    ss_path = os.path.join(save_dir, "screenshot.png")
    take_screenshot(serial, output_path=ss_path)
    print(f"  Screenshot saved: {ss_path}")

    # Extract response text
    response_text = extract_response_text(serial, "Gemini")
    text_path = os.path.join(save_dir, "response.txt")
    with open(text_path, "w") as f:
        f.write(response_text)
    print(f"  Response text saved: {text_path}")

    # Save metadata
    meta = {
        "client": client,
        "prompt": prompt,
        "platform": "Gemini",
        "mode": "adb",
        "device": serial,
        "timestamp": timestamp,
    }
    with open(os.path.join(save_dir, "metadata.json"), "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\n  Results in: {save_dir}/")
    return save_dir


# ── Appium Audit Flow ────────────────────────────────────────────────────────

def run_audit_appium(serial, client, output_dir, port=4723):
    from appium import webdriver
    from appium.options.android.uiautomator2.base import UiAutomator2Options
    from appium.webdriver.common.appiumby import AppiumBy
    from flows import (
        dismiss_first_run_dialogs, navigate_to_url as appium_navigate,
        switch_to_webview, switch_to_native, webview_find, webview_set_text,
        wait_for_generation as appium_wait_gen, tap_optional,
        clear_chrome as appium_clear, adb_scroll,
    )

    prompt = build_audit_prompt(client)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    save_dir = os.path.join(output_dir, "Gemini", timestamp)
    os.makedirs(save_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"GEMINI AUDIT (Appium) — {client['biz_name']}")
    print(f"Keyword: {client['keyword']}")
    print(f"Prompt: {prompt[:80]}...")
    print(f"Device: {serial}")
    print(f"{'='*60}")

    # 1. Clear Chrome
    print("\n[1/8] Clearing Chrome...")
    appium_clear(serial)
    time.sleep(1)

    # 2. Appium session — use app_package/app_activity (not browser_name)
    # This launches Chrome as a native app so Appium can handle FRE dialogs
    print("[2/8] Starting Appium session...")
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

    try:
        # 3. FRE
        print("[3/8] Dismissing FRE...")
        dismiss_first_run_dialogs(driver)

        # 4. Navigate
        print("[4/8] Navigating to Gemini...")
        appium_navigate(driver, "gemini.google.com")
        tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                     'new UiSelector().text("No thanks")', timeout=3)

        # 5. WebView + type
        print("[5/8] Switching to WebView...")
        time.sleep(3)
        if not switch_to_webview(driver):
            raise RuntimeError("WebView context not available")

        # Dismiss banner via JS
        driver.execute_script("""
            document.querySelectorAll('button[aria-label="Close"], button[aria-label="Dismiss"]')
                .forEach(btn => { if (btn.offsetParent !== null) btn.click(); });
        """)
        time.sleep(1)

        print("[6/8] Typing audit prompt...")
        input_el = webview_find(driver, [
            "div[contenteditable='true']",
            "textarea",
            "[role='textbox']",
        ], timeout=15)
        input_el.click()
        time.sleep(0.3)
        webview_set_text(driver, input_el, prompt)

        # 6. Send
        print("[7/8] Sending...")
        send_btn = webview_find(driver, [
            "button[aria-label='Send message']",
            "button[aria-label*='Send']",
        ], timeout=10)
        send_btn.click()

        # 7. Wait
        print("[8/8] Waiting for AI response...")
        appium_wait_gen(driver, "Gemini")
        time.sleep(3)

        # Dismiss "Chat with Gemini in an app" banner — native X button
        print("  Dismissing Gemini app banner...")
        switch_to_native(driver)
        # Try tapping the X button on the banner
        tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                     'new UiSelector().description("Close")', timeout=3)
        tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                     'new UiSelector().text("Close")', timeout=2)
        # Also try the X icon directly — it's at the left side of the banner
        tap_optional(driver, AppiumBy.XPATH,
                     '//android.widget.ImageView[@content-desc="Close"]', timeout=2)
        time.sleep(1)

        # Switch back to WebView for scroll + text
        switch_to_webview(driver)
        time.sleep(1)

        # Dismiss banner — tap X via ADB at its known position
        # The X button is at the left side of the "Chat with Gemini" banner
        print("  Tapping banner X via ADB...")
        w, h = get_screen_size(serial)
        # X button is roughly at x=65, y=370 on 720x1600 — scale proportionally
        x_pos = int(w * 0.09)
        y_pos = int(h * 0.23)
        subprocess.run(
            ["adb", "-s", serial, "shell", "input", "tap", str(x_pos), str(y_pos)],
            capture_output=True, timeout=5,
        )
        time.sleep(1)
        # Tap again in case first didn't register
        subprocess.run(
            ["adb", "-s", serial, "shell", "input", "tap", str(x_pos), str(y_pos)],
            capture_output=True, timeout=5,
        )
        time.sleep(1)

        # Scroll response to top via JS
        print("  Positioning response to top...")
        driver.execute_script("""
            let el = document.querySelector(
                'model-response, message-content, .model-response-text'
            );
            if (!el) {
                let all = document.querySelectorAll('[class*="response"], [class*="model"]');
                for (let a of all) {
                    if (a.innerText && a.innerText.length > 50) { el = a; break; }
                }
            }
            if (el) el.scrollIntoView({behavior: 'instant', block: 'start'});
        """)
        time.sleep(1)

        # Extract response text via JS
        response_text = driver.execute_script("""
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
        """)

        switch_to_native(driver)

        # Screenshot via ADB while Chrome is still on screen
        time.sleep(1)
        ss_path = os.path.join(save_dir, "screenshot.png")
        take_screenshot(serial, output_path=ss_path)
        print(f"  Screenshot saved: {ss_path}")

        # Save response text
        text_path = os.path.join(save_dir, "response.txt")
        with open(text_path, "w") as f:
            f.write(response_text or "")
        print(f"  Response text saved: {text_path}")

    finally:
        driver.quit()

    meta = {
        "client": client,
        "prompt": prompt,
        "platform": "Gemini",
        "mode": "appium",
        "device": serial,
        "timestamp": timestamp,
    }
    with open(os.path.join(save_dir, "metadata.json"), "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\n  Results in: {save_dir}/")
    return save_dir


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Test Gemini audit flow")
    parser.add_argument("--mode", choices=["adb", "appium"], default="adb")
    parser.add_argument("--serial", help="Override device serial")
    parser.add_argument("--port", type=int, default=4723, help="Appium port")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if args.mode == "adb":
        serial = args.serial or INFINIX_SERIAL
        run_audit_adb(serial, TEST_CLIENT, OUTPUT_DIR)
    else:
        serial = args.serial or SAMSUNG_SERIAL
        run_audit_appium(serial, TEST_CLIENT, OUTPUT_DIR, port=args.port)

    print("\nDone!")


if __name__ == "__main__":
    main()
