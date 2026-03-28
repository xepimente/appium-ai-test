"""
AEO Appium Flows — v2.0
-----------------------
Platform-specific Appium flows for Gemini, ChatGPT, Perplexity.

Strategy:
  - NATIVE_APP context: Chrome FRE dialogs + address bar navigation
  - WEBVIEW_chrome context: interact with web page elements using CSS selectors
  - ADB subprocess: scrolling (12 swipes, 400px, 700ms, 2.2s pause)

This avoids all the StaleElement / native-selector issues by using real DOM access.
"""

import subprocess
import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, StaleElementReferenceException,
)


# ── Constants ─────────────────────────────────────────────────────────────────

CHROME_PACKAGE    = "com.android.chrome"
DEFAULT_WAIT      = 10
PAGE_LOAD_WAIT    = 10
GENERATION_TIMEOUT = 120   # max seconds to wait for AI to finish generating
GENERATION_POLL    = 3     # seconds between checks


# ── ADB Helpers ───────────────────────────────────────────────────────────────

def clear_chrome(serial):
    """Clear Chrome app data and lock portrait orientation via ADB."""
    subprocess.run(
        ["adb", "-s", serial, "shell", "pm", "clear", CHROME_PACKAGE],
        capture_output=True, text=True, timeout=15,
    )
    # Disable auto-rotate and force portrait
    subprocess.run(
        ["adb", "-s", serial, "shell", "settings", "put", "system", "accelerometer_rotation", "0"],
        capture_output=True, timeout=5,
    )
    subprocess.run(
        ["adb", "-s", serial, "shell", "settings", "put", "system", "user_rotation", "0"],
        capture_output=True, timeout=5,
    )


def adb_scroll(serial, swipes=12, px=400, duration_ms=700, pause_s=2.2):
    """
    Scroll down via ADB input swipe.
    Swipe in the middle of the content area (above the input bar).
    """
    start_y = 1100
    end_y   = start_y - px
    for i in range(swipes):
        subprocess.run(
            ["adb", "-s", serial, "shell",
             "input", "swipe", "15", str(start_y), "15", str(end_y), str(duration_ms)],
            capture_output=True, timeout=10,
        )
        time.sleep(pause_s)


# ── Context Switching ─────────────────────────────────────────────────────────

def switch_to_webview(driver, timeout=20):
    """Switch to WEBVIEW_chrome context. Returns True if successful."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        contexts = driver.contexts
        for ctx in contexts:
            if "WEBVIEW" in ctx:
                driver.switch_to.context(ctx)
                return True
        time.sleep(1)
    return False


def switch_to_native(driver):
    """Switch back to NATIVE_APP context."""
    driver.switch_to.context("NATIVE_APP")


# ── Native Element Helpers (for Chrome UI) ────────────────────────────────────

def tap_optional(driver, by, value, timeout=3):
    """Tap an element if present, silently skip if not found or on any error."""
    try:
        el = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((by, value))
        )
        el.click()
        time.sleep(0.5)
        return True
    except Exception:
        return False


def dismiss_first_run_dialogs(driver):
    """
    Dismiss Chrome first-run experience (FRE) dialogs.
    Must be in NATIVE_APP context.
    """
    # Screen 1: sign-in prompt
    tap_optional(driver, AppiumBy.ID,
                 "com.android.chrome:id/signin_fre_dismiss_button", timeout=5)
    tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                 'new UiSelector().text("Use without an account")', timeout=2)
    tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                 'new UiSelector().text("Stay signed out")', timeout=2)

    # Screen 2: notifications prompt
    tap_optional(driver, AppiumBy.ID,
                 "com.android.chrome:id/negative_button", timeout=3)
    tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                 'new UiSelector().text("No thanks")', timeout=2)

    # Screen 3: "Enhanced ad privacy in Chrome" — can take a few seconds to appear
    tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                 'new UiSelector().text("Got it")', timeout=5)
    tap_optional(driver, AppiumBy.ID,
                 'com.android.chrome:id/ack_button', timeout=3)

    # Screen 4: other possible dialogs
    tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                 'new UiSelector().text("Accept & continue")', timeout=3)
    tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                 'new UiSelector().text("OK")', timeout=2)
    tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                 'new UiSelector().text("Continue")', timeout=2)
    tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                 'new UiSelector().text("Got it")', timeout=2)

    # Second pass: some devices show notifications AFTER ad privacy (e.g. Infinix)
    tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                 'new UiSelector().text("No thanks")', timeout=3)
    tap_optional(driver, AppiumBy.ID,
                 "com.android.chrome:id/negative_button", timeout=2)
    time.sleep(0.5)


def navigate_to_url(driver, url):
    """
    Tap Chrome address bar and navigate to URL.
    Must be in NATIVE_APP context.
    """
    search_box = None
    for selector in [
        (AppiumBy.ID, "com.android.chrome:id/search_box_text"),
        (AppiumBy.ID, "com.android.chrome:id/url_bar"),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("Search Google")'),
    ]:
        try:
            search_box = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located(selector)
            )
            break
        except TimeoutException:
            continue

    if not search_box:
        raise RuntimeError("Could not find Chrome address bar")

    search_box.click()
    time.sleep(0.8)

    # After tap, Chrome shows url_bar — type URL there
    url_bar = None
    for selector in [
        (AppiumBy.ID, "com.android.chrome:id/url_bar"),
        (AppiumBy.ID, "com.android.chrome:id/search_box_text"),
    ]:
        try:
            url_bar = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located(selector)
            )
            break
        except TimeoutException:
            continue

    target = url_bar or search_box
    target.clear()
    target.send_keys(url)
    time.sleep(0.5)

    # Tap first autocomplete suggestion, or press Enter
    try:
        suggestion = WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((
                AppiumBy.ANDROID_UIAUTOMATOR,
                f'new UiSelector().resourceId("com.android.chrome:id/line_1").text("{url}")'
            ))
        )
        suggestion.click()
    except TimeoutException:
        driver.press_keycode(66)

    time.sleep(5)


# ── Wait for AI Generation ─────────────────────────────────────────────────────

def wait_for_generation(driver, platform, timeout=GENERATION_TIMEOUT):
    """
    Wait until the AI platform finishes generating its response.
    Detects this by checking if the 'Stop generating' button disappears.
    Must be called in WEBVIEW context.

    Each platform has a different stop button:
      ChatGPT:    button[aria-label='Stop streaming'] or button with 'Stop' text
      Gemini:     button[aria-label='Stop response'] or similar
      Perplexity: button with stop/loading indicator
    """
    # CSS selectors that indicate generation is still in progress
    stop_selectors = {
        "ChatGPT": [
            "button[aria-label='Stop streaming']",
            "button[aria-label='Stop generating']",
            "button[data-testid='stop-button']",
        ],
        "Gemini": [
            "button[aria-label='Stop response']",
            "button[aria-label='Stop']",
            "mat-icon[data-mat-icon-name='stop_circle']",
        ],
        "Perplexity": [
            "button[aria-label='Stop']",
            "button.stop-button",
        ],
    }

    selectors = stop_selectors.get(platform, [])
    if not selectors:
        time.sleep(PAGE_LOAD_WAIT)
        return

    # First, wait a few seconds for generation to START (stop button to appear)
    time.sleep(3)

    # Then poll until stop button disappears (generation finished)
    deadline = time.time() + timeout
    while time.time() < deadline:
        still_generating = False
        for css in selectors:
            try:
                el = driver.find_element(By.CSS_SELECTOR, css)
                if el.is_displayed():
                    still_generating = True
                    break
            except (NoSuchElementException, StaleElementReferenceException):
                continue

        if not still_generating:
            # Double-check: wait 2s and verify it's really done
            time.sleep(2)
            still_going = False
            for css in selectors:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, css)
                    if el.is_displayed():
                        still_going = True
                        break
                except (NoSuchElementException, StaleElementReferenceException):
                    continue
            if not still_going:
                return  # generation complete

        time.sleep(GENERATION_POLL)

    # Timeout — generation took too long, proceed anyway


# ── WebView Text Input ────────────────────────────────────────────────────────

def webview_set_text(driver, element, text):
    """
    Set text on a web element via JS. Works for textarea, input, and
    contenteditable elements. Uses execCommand('insertText') for
    contenteditable — this triggers React/ProseMirror state correctly.
    """
    driver.execute_script("""
        let el = arguments[0];
        let text = arguments[1];
        el.focus();
        if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
            // Native setter triggers React's synthetic events
            let nativeSetter = Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype, 'value'
            )?.set || Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            )?.set;
            if (nativeSetter) {
                nativeSetter.call(el, text);
            } else {
                el.value = text;
            }
            el.dispatchEvent(new Event('input', {bubbles: true}));
        } else {
            // contenteditable: select all + insertText replaces content
            // without innerHTML (blocked by Trusted Types on some sites)
            document.execCommand('selectAll', false, null);
            document.execCommand('insertText', false, text);
        }
    """, element, text)
    time.sleep(0.5)


def webview_find(driver, css_selectors, timeout=DEFAULT_WAIT):
    """
    Try multiple CSS selectors in WebView context, return first found.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for css in css_selectors:
            try:
                el = driver.find_element(By.CSS_SELECTOR, css)
                if el.is_displayed():
                    return el
            except (NoSuchElementException, StaleElementReferenceException):
                continue
        time.sleep(0.5)
    raise TimeoutException(f"None of {len(css_selectors)} CSS selectors found within {timeout}s")


# ── Platform Flows ─────────────────────────────────────────────────────────────

def run_gemini(driver, serial, prompt, follow_up=None, backlinks=None):
    """Gemini (gemini.google.com) flow."""
    steps = []

    # ── Native: FRE + navigate ──
    dismiss_first_run_dialogs(driver)
    steps.append("dismissed_first_run")

    navigate_to_url(driver, "gemini.google.com")
    steps.append("navigated_to_gemini")

    # Dismiss optional popup in native context
    tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                 'new UiSelector().text("No thanks")', timeout=3)
    tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                 'new UiSelector().text("Try it")', timeout=2)

    # ── Switch to WebView ──
    time.sleep(3)
    if not switch_to_webview(driver):
        return {"status": "error", "error": "WebView context not available", "steps": steps}
    steps.append("switched_to_webview")

    # Find chat input
    input_el = webview_find(driver, [
        "div[contenteditable='true']",
        "textarea",
        "[role='textbox']",
        "rich-textarea div[contenteditable='true']",
    ], timeout=15)
    steps.append("found_input")

    # Type prompt via JS
    input_el.click()
    time.sleep(0.3)
    webview_set_text(driver, input_el, prompt)
    steps.append("typed_prompt")

    # Click send button
    send_btn = webview_find(driver, [
        "button[aria-label='Send message']",
        "button[aria-label*='Send']",
        "button[data-testid='send-button']",
        "button.send-button",
    ], timeout=10)
    send_btn.click()
    steps.append("sent_prompt")

    # Wait for AI to finish generating (stay in WebView to detect stop button)
    wait_for_generation(driver, "Gemini")
    steps.append("generation_complete")

    # ADB scroll works from any context
    adb_scroll(serial)
    steps.append("scrolled")

    # Follow-up — only after generation is confirmed done
    if follow_up and follow_up.strip():
        time.sleep(2)
        input_el2 = webview_find(driver, [
            "div[contenteditable='true']",
            "textarea",
            "[role='textbox']",
        ], timeout=15)
        input_el2.click()
        time.sleep(0.3)
        webview_set_text(driver, input_el2, follow_up)
        send_btn2 = webview_find(driver, [
            "button[aria-label='Send message']",
            "button[aria-label*='Send']",
        ], timeout=10)
        send_btn2.click()
        steps.append("sent_followup")
        wait_for_generation(driver, "Gemini")
        steps.append("followup_generation_complete")
        adb_scroll(serial)
        steps.append("scrolled_followup")

    # Backlink click (Type 3) — after all scrolling is done
    if backlinks:
        matched = click_backlink(driver, serial, backlinks)
        if matched:
            steps.append(f"backlink_clicked:{matched}")

    switch_to_native(driver)
    return {"status": "success", "steps": steps}


def run_chatgpt(driver, serial, prompt, follow_up=None, backlinks=None):
    """ChatGPT (chatgpt.com) flow."""
    steps = []

    # ── Native: FRE + navigate ──
    dismiss_first_run_dialogs(driver)
    steps.append("dismissed_first_run")

    navigate_to_url(driver, "chatgpt.com")
    steps.append("navigated_to_chatgpt")

    # ── Switch to WebView (ChatGPT needs ~10s to load) ──
    time.sleep(5)
    if not switch_to_webview(driver):
        return {"status": "error", "error": "WebView context not available", "steps": steps}
    steps.append("switched_to_webview")

    # Find input: #prompt-textarea
    input_el = webview_find(driver, [
        "#prompt-textarea",
        "textarea[placeholder*='Ask']",
        "textarea",
        "div[contenteditable='true']",
    ], timeout=20)
    steps.append("found_input")

    # Type prompt
    input_el.click()
    time.sleep(0.3)
    webview_set_text(driver, input_el, prompt)
    steps.append("typed_prompt")

    # Click send: #composer-submit-button
    send_btn = webview_find(driver, [
        "#composer-submit-button",
        "button[aria-label='Send prompt']",
        "button[data-testid='send-button']",
        "button[type='submit']",
    ], timeout=10)
    send_btn.click()
    steps.append("sent_prompt")

    # Wait for AI to finish generating (detect stop button disappearing)
    wait_for_generation(driver, "ChatGPT")
    steps.append("generation_complete")

    # ADB scroll
    adb_scroll(serial)
    steps.append("scrolled")

    # Follow-up — only after generation is confirmed done
    if follow_up and follow_up.strip():
        time.sleep(2)
        input_el2 = webview_find(driver, [
            "#prompt-textarea",
            "textarea",
            "div[contenteditable='true']",
        ], timeout=15)
        input_el2.click()
        time.sleep(0.3)
        webview_set_text(driver, input_el2, follow_up)
        send_btn2 = webview_find(driver, [
            "#composer-submit-button",
            "button[aria-label='Send prompt']",
            "button[type='submit']",
        ], timeout=10)
        send_btn2.click()
        steps.append("sent_followup")
        wait_for_generation(driver, "ChatGPT")
        steps.append("followup_generation_complete")
        adb_scroll(serial)
        steps.append("scrolled_followup")

    # Backlink click (Type 3)
    if backlinks:
        matched = click_backlink(driver, serial, backlinks)
        if matched:
            steps.append(f"backlink_clicked:{matched}")

    switch_to_native(driver)
    return {"status": "success", "steps": steps}


def run_perplexity(driver, serial, prompt, follow_up=None, backlinks=None):
    """Perplexity (www.perplexity.ai) flow."""
    steps = []

    # ── Native: FRE + navigate ──
    dismiss_first_run_dialogs(driver)
    steps.append("dismissed_first_run")

    navigate_to_url(driver, "www.perplexity.ai")
    steps.append("navigated_to_perplexity")

    # Dismiss Comet promo modals + login wall (native)
    time.sleep(3)
    tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                 'new UiSelector().text("Close")', timeout=4)
    time.sleep(0.5)
    tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                 'new UiSelector().text("Close")', timeout=3)
    # Dismiss "X" close button on login/app promo overlays
    tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                 'new UiSelector().description("Close")', timeout=2)
    tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                 'new UiSelector().text("Maybe later")', timeout=2)
    tap_optional(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                 'new UiSelector().text("Not now")', timeout=2)

    # ── Switch to WebView ──
    time.sleep(2)
    if not switch_to_webview(driver):
        return {"status": "error", "error": "WebView context not available", "steps": steps}
    steps.append("switched_to_webview")

    # Dismiss ALL modals/banners/login walls via JS
    driver.execute_script("""
        // Close buttons (Comet promo, login wall, app promo)
        document.querySelectorAll('button[aria-label="Close"], button[aria-label="Dismiss"], [data-dismiss], .close-button').forEach(btn => {
            if (btn.offsetParent !== null) btn.click();
        });
        // "X" SVG close buttons (common in modals)
        document.querySelectorAll('svg').forEach(svg => {
            let parent = svg.closest('button');
            if (parent && parent.offsetParent !== null && svg.querySelector('path') && !parent.textContent.trim()) {
                parent.click();
            }
        });
    """)
    time.sleep(1)
    # Second pass
    driver.execute_script("""
        document.querySelectorAll('button[aria-label="Close"], button[aria-label="Dismiss"]').forEach(btn => {
            if (btn.offsetParent !== null) btn.click();
        });
    """)
    time.sleep(0.5)

    # Find input: #ask-input (contenteditable div on Perplexity)
    input_el = webview_find(driver, [
        "#ask-input",
        "div[contenteditable='true']",
        "textarea",
    ], timeout=15)
    steps.append("found_input")

    # Focus and type via JS — contenteditable needs JS focus first
    driver.execute_script("arguments[0].focus(); arguments[0].click();", input_el)
    time.sleep(0.3)
    webview_set_text(driver, input_el, prompt)
    steps.append("typed_prompt")

    # Click Submit via JS (bypasses any overlay interception)
    send_btn = webview_find(driver, [
        "button[aria-label='Submit']",
        "button[type='submit']",
    ], timeout=10)
    driver.execute_script("arguments[0].disabled = false; arguments[0].click();", send_btn)
    steps.append("sent_prompt")

    # Wait for AI to finish generating
    wait_for_generation(driver, "Perplexity")
    steps.append("generation_complete")

    # ADB scroll
    adb_scroll(serial)
    steps.append("scrolled")

    # Follow-up — only after generation done
    if follow_up and follow_up.strip():
        time.sleep(2)
        input_el2 = webview_find(driver, [
            "#ask-input",
            "div[contenteditable='true']",
            "textarea",
        ], timeout=15)
        driver.execute_script("arguments[0].focus(); arguments[0].click();", input_el2)
        time.sleep(0.3)
        webview_set_text(driver, input_el2, follow_up)
        send_btn2 = webview_find(driver, [
            "button[aria-label='Submit']",
            "button[type='submit']",
        ], timeout=10)
        driver.execute_script("arguments[0].disabled = false; arguments[0].click();", send_btn2)
        steps.append("sent_followup")
        wait_for_generation(driver, "Perplexity")
        steps.append("followup_generation_complete")
        adb_scroll(serial)
        steps.append("scrolled_followup")

    # Backlink click (Type 3)
    if backlinks:
        matched = click_backlink(driver, serial, backlinks)
        if matched:
            steps.append(f"backlink_clicked:{matched}")

    switch_to_native(driver)
    return {"status": "success", "steps": steps}


# ── Backlink Click (Type 3) ────────────────────────────────────────────────────

def click_backlink(driver, serial, backlinks, cdp_port=9222):
    """
    Find and click a matching backlink in the AI response sources.

    Strategy: CDP first (scans entire page DOM including off-screen content
    from first response), then Sources panel, then fallback to Appium JS.

    After clicking, stays on the backlink page for ~5 seconds with scrolling.

    Args:
        driver: Appium WebDriver
        serial: device serial (for ADB scroll + CDP)
        backlinks: list of backlink URLs to look for
        cdp_port: CDP port for this device

    Returns:
        The matched URL string, or None if no match found.
    """
    if not backlinks:
        return None

    import json
    import random
    from screenshot import cdp_connect, cdp_disconnect, cdp_eval

    target = random.choice(backlinks)
    domain = target.split("//")[-1].split("/")[0].replace("www.", "")
    print(f"  Looking for backlink matching: {domain}")

    backlinks_js = json.dumps(backlinks)

    search_js = f"""
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
    """

    # ── Step 1: CDP scan entire page DOM ──
    print("    Scanning entire page DOM for backlink via CDP...")
    ws = cdp_connect(serial, cdp_port)
    if ws:
        try:
            matched = cdp_eval(ws, search_js)
        finally:
            cdp_disconnect(serial, ws, cdp_port)

        if matched:
            print(f"    Backlink found in page ({matched.get('match')}): {matched.get('url', '')[:60]}")
            print(f"  Waiting for backlink page to load...")
            time.sleep(8)
            print(f"  Browsing backlink page for ~5 seconds...")
            adb_scroll(serial, swipes=2, px=300, duration_ms=500, pause_s=1.0)
            time.sleep(2)
            return matched.get("url", domain)
        print("    No backlink in page DOM — trying Sources panel...")

    # ── Step 2: Open Sources panel via CDP ──
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
        finally:
            cdp_disconnect(serial, ws_src, cdp_port)

        time.sleep(4)

        # ── Step 3: Search sources panel ──
        ws2 = cdp_connect(serial, cdp_port)
        if ws2:
            try:
                print("    Searching sources panel for matching backlink...")
                matched = cdp_eval(ws2, search_js)
            finally:
                cdp_disconnect(serial, ws2, cdp_port)

            if matched:
                print(f"    Backlink found ({matched.get('match')}): {matched.get('url', '')[:60]}")
                print(f"  Waiting for backlink page to load...")
                time.sleep(8)
                print(f"  Browsing backlink page for ~5 seconds...")
                adb_scroll(serial, swipes=2, px=300, duration_ms=500, pause_s=1.0)
                time.sleep(2)
                return matched.get("url", domain)

    print(f"    No matching backlink found (looking for {domain})")
    return None


# ── Flow Dispatcher ────────────────────────────────────────────────────────────

FLOW_MAP = {
    "Gemini":     run_gemini,
    "ChatGPT":    run_chatgpt,
    "Perplexity": run_perplexity,
}


def run_flow(platform, driver, serial, prompt, follow_up=None, backlinks=None):
    """
    Dispatch to the correct platform flow.
    Returns {"status": "success"/"error", "steps": [...], "error": "..."}
    """
    flow_fn = FLOW_MAP.get(platform)
    if not flow_fn:
        return {"status": "error", "error": f"Unknown platform: {platform}", "steps": []}

    try:
        return flow_fn(driver, serial, prompt, follow_up, backlinks=backlinks)
    except TimeoutException as e:
        return {"status": "error", "error": f"Element not found: {e}", "steps": []}
    except Exception as e:
        return {"status": "error", "error": str(e), "steps": []}
