"""
u2_flows.py — AEO Platform Flows via uiautomator2
===================================================
Mirrors the exact Maestro YAML flow logic step-by-step:
  1. launchApp clearState
  2. Dismiss Chrome dialogs (optional taps)
  3. Tap search bar (^Search.*)
  4. Input PLATFORM_NAME -> Enter
  5. Scroll until "Search Results" visible
  6. Tap first organic result below "Search Results"
  7. Dismiss banners (optional)
  8. Input PROMPT -> tap submit
  9. Wait/scroll to read response
  10. (Optional) Input FOLLOW_UP -> tap submit -> wait/scroll

True parallel: each device gets its own u2 connection.
No JVM, no port conflicts, no shared ADB server locks.

Usage:
    python3 u2_flows.py <serial> <platform> "<prompt>" ["<follow_up>"]
    python3 u2_flows.py 324651961440 Gemini "best plumber in SF"
"""

import time
import os
import traceback
from datetime import datetime, timezone


# ── Platform Config ───────────────────────────────────────────────────────────
# Matches the --env vars from Maestro YAML:
#   PLATFORM_NAME, PLATFORM_URL, PLATFORM_DOMAIN, PLATFORM_TITLE

PLATFORM_CONFIG = {
    "ChatGPT": {
        "PLATFORM_NAME":   "ChatGPT",
        "PLATFORM_URL":    "https://chat.openai.com",
        "PLATFORM_DOMAIN": "chatgpt.com",
        "PLATFORM_TITLE":  "ChatGPT",
        "input_selectors": [
            {"description": "Message ChatGPT"},
            {"textContains": "Message ChatGPT"},
            {"resourceId": "prompt-textarea"},
        ],
        "submit_selectors": [
            {"description": "Send prompt"},              # exact match from dump
            {"resourceId": "composer-submit-button"},
            {"description": "Send message"},
        ],
        "followup_input_selectors": [
            {"description": "Message ChatGPT"},
            {"textContains": "Message ChatGPT"},
            {"resourceId": "prompt-textarea"},
        ],
    },
    "Gemini": {
        "PLATFORM_NAME":   "Google Gemini",
        "PLATFORM_URL":    "https://gemini.google.com",
        "PLATFORM_DOMAIN": "gemini.google.com",
        "PLATFORM_TITLE":  "Google Gemini",
        "input_selectors": [
            {"textContains": "Enter a prompt"},
            {"textContains": "Ask Gemini"},
            {"description": "Enter a prompt here"},
        ],
        "submit_selectors": [
            {"description": "Send message"},
            {"contentDescription": "Send message"},
            {"description": "Submit"},
        ],
        "followup_input_selectors": [
            {"textContains": "Enter a prompt"},
            {"textContains": "Ask Gemini"},
        ],
    },
    "Perplexity": {
        "PLATFORM_NAME":   "Perplexity AI",
        "PLATFORM_URL":    "https://www.perplexity.ai",
        "PLATFORM_DOMAIN": "perplexity.ai",
        "PLATFORM_TITLE":  "Perplexity",
        "input_selectors": [
            {"textContains": "Ask anything"},
            {"textContains": "Ask follow-up"},
            {"description": "Ask anything..."},
        ],
        "submit_selectors": [
            {"description": "Submit"},
            {"description": "Send"},
            {"contentDescription": "Submit"},
        ],
        "followup_input_selectors": [
            {"textContains": "Ask follow-up"},
            {"textContains": "Ask anything"},
        ],
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def screenshot(d, device_id, step_name):
    """Save a debug screenshot."""
    debug_dir = f"/tmp/u2-debug-{device_id}"
    os.makedirs(debug_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%H%M%S")
    path = f"{debug_dir}/{ts}_{step_name}.png"
    try:
        d.screenshot(path)
    except Exception:
        pass
    return path


def type_text(d, text):
    """
    Type text into the currently focused field.
    Uses clipboard paste (handles all characters).
    Falls back to ADB input text.
    """
    if not text:
        return

    # Clear existing text
    try:
        d.shell("input keyevent KEYCODE_MOVE_HOME")
        d.shell("input keyevent --longpress KEYCODE_MOVE_END")
        time.sleep(0.1)
        d.shell("input keyevent KEYCODE_DEL")
        time.sleep(0.1)
    except Exception:
        pass

    # Method 1: Clipboard paste (best — handles all characters)
    try:
        d.set_clipboard(text)
        time.sleep(0.2)
        d.shell("input keyevent 279")  # KEYCODE_PASTE
        time.sleep(0.3)
        return
    except Exception:
        pass

    # Method 2: ADB input text (ASCII only)
    try:
        escaped = text.replace(" ", "%s")
        escaped = escaped.replace("'", "\\'")
        escaped = escaped.replace('"', '\\"')
        escaped = escaped.replace("&", "\\&")
        escaped = escaped.replace("|", "\\|")
        escaped = escaped.replace(";", "\\;")
        escaped = escaped.replace("(", "\\(")
        escaped = escaped.replace(")", "\\)")
        d.shell(f"input text '{escaped}'")
        time.sleep(0.3)
    except Exception as e:
        print(f"      type_text failed: {e}")


def find_and_click(d, selectors, timeout=5, label="element"):
    """Try multiple selectors, click the first match. Returns True if clicked."""
    for sel in selectors:
        try:
            el = d(**sel)
            if el.exists(timeout=timeout):
                el.click()
                time.sleep(0.5)
                return True
        except Exception:
            continue
    return False


def wait_for_animation(d, seconds=2):
    """Equivalent to Maestro's waitForAnimationToEnd."""
    time.sleep(seconds)


# ── AEO Flow ─────────────────────────────────────────────────────────────────

def run_aeo_flow(d, platform, prompt, follow_up=None, device_id="unknown"):
    """
    Run a complete AEO session — mirrors the Maestro YAML exactly.

    Steps (matching YAML):
      1. launchApp com.android.chrome clearState: true
      2. tapOn "Use without an account" (optional)
      3. tapOn "Got it" (optional)
      4. tapOn "Stay signed out" (optional)
      5. waitForAnimationToEnd
      6. tapOn "^Search.*"
      7. waitForAnimationToEnd
      8. inputText PLATFORM_NAME
      9. pressKey Enter
      10. waitForAnimationToEnd
      11. scrollUntilVisible "Search Results" DOWN
      12. tapOn ".*" below "Search Results" index 1
      13. tapOn "No thanks" (optional)
      14. waitForAnimationToEnd
      15. inputText PROMPT
      16. tapOn submit button
      17. waitForAnimationToEnd
      18. scroll x5 (read response)
      19. [if follow_up] tapOn input -> inputText FOLLOW_UP -> submit -> scroll x5

    Returns: {"status": "success"|"error", "error": "...", "steps": [...]}
    """

    config = PLATFORM_CONFIG.get(platform)
    if not config:
        return {"status": "error", "error": f"Unknown platform: {platform}", "steps": []}

    steps = []

    def log(step, status="ok", detail=""):
        entry = {"step": step, "status": status, "detail": detail}
        steps.append(entry)
        icon = "ok" if status == "ok" else "FAIL" if status == "fail" else "WARN"
        print(f"      [{icon}] [{device_id}] {step}" + (f" -- {detail}" if detail else ""))

    try:
        # ==============================================================
        # STEP 1: launchApp com.android.chrome clearState: true
        # ==============================================================
        print(f"   [{device_id}] Clearing Chrome state...")
        try:
            d.app_clear("com.android.chrome")
        except Exception:
            d.shell("pm clear com.android.chrome")
        time.sleep(1)

        d.app_start("com.android.chrome")
        time.sleep(3)
        log("launchApp com.android.chrome clearState")

        # ==============================================================
        # STEP 2-4: Dismiss Chrome dialogs (all optional)
        # ==============================================================
        chrome_dialogs = [
            "Accept & continue",
            "Use without an account",
            "No thanks",
            "No, thanks",
            "Got it",
            "Stay signed out",
            "Done",
            "OK",
        ]

        for dialog_text in chrome_dialogs:
            try:
                el = d(text=dialog_text)
                if el.exists(timeout=1.5):
                    el.click()
                    time.sleep(1)
                    log(f"tapOn (optional) \"{dialog_text}\"")
            except Exception:
                pass

        # Also try textContains for partial matches
        for partial in ["Accept", "No thanks"]:
            try:
                el = d(textContains=partial)
                if el.exists(timeout=1):
                    el.click()
                    time.sleep(1)
                    log(f"tapOn (optional) textContains \"{partial}\"")
            except Exception:
                pass

        # ==============================================================
        # STEP 5: waitForAnimationToEnd
        # ==============================================================
        wait_for_animation(d)
        log("waitForAnimationToEnd")

        # ==============================================================
        # STEP 6: tapOn text: ^Search.*
        # ==============================================================
        search_tapped = False

        for sel in [
            {"resourceId": "com.android.chrome:id/search_box_text"},
            {"resourceId": "com.android.chrome:id/url_bar"},
            {"textContains": "Search or type"},
            {"description": "Search or type web address"},
        ]:
            try:
                el = d(**sel)
                if el.exists(timeout=3):
                    el.click()
                    time.sleep(1)
                    search_tapped = True
                    log("tapOn ^Search.*", detail=str(sel))
                    break
            except Exception:
                continue

        if not search_tapped:
            w, h = d.window_size()
            d.click(w // 2, int(h * 0.12))
            time.sleep(1)
            log("tapOn ^Search.*", detail="fallback coordinates")

        # ==============================================================
        # STEP 7: waitForAnimationToEnd
        # ==============================================================
        wait_for_animation(d, 1)
        log("waitForAnimationToEnd")

        # ==============================================================
        # STEP 8: inputText PLATFORM_NAME
        # ==============================================================
        platform_name = config["PLATFORM_NAME"]
        type_text(d, platform_name)
        log(f"inputText \"{platform_name}\"")

        # ==============================================================
        # STEP 9: pressKey Enter
        # ==============================================================
        d.press("enter")
        log("pressKey Enter")

        # ==============================================================
        # STEP 10: waitForAnimationToEnd
        # ==============================================================
        wait_for_animation(d, 4)
        log("waitForAnimationToEnd")

        # ==============================================================
        # STEP 11: scrollUntilVisible "Search Results" DOWN
        # ==============================================================
        search_results_found = False
        for scroll_attempt in range(8):
            try:
                el = d(text="Search Results")
                if el.exists(timeout=2):
                    search_results_found = True
                    log("scrollUntilVisible \"Search Results\"",
                        detail=f"found after {scroll_attempt} scrolls")
                    break
            except Exception:
                pass

            d.swipe_ext("up", scale=0.4)
            time.sleep(1.5)

        if not search_results_found:
            log("scrollUntilVisible \"Search Results\"", status="warn",
                detail="not found")

        # ==============================================================
        # STEP 12: tapOn text: ".*" below: "Search Results" index: 1
        #          Replicates Maestro's exact behavior:
        #          - Find all elements WITH TEXT below "Search Results"
        #          - Tap the one at index 1 (second match)
        # ==============================================================
        result_tapped = False

        if search_results_found:
            try:
                sr_el = d(text="Search Results")
                sr_bounds = sr_el.info["bounds"]
                sr_bottom = sr_bounds["bottom"]

                # Get ALL elements that have text (Maestro's text: ".*")
                # and are positioned below "Search Results"
                all_els = d.xpath('//*[@text!=""]').all()
                candidates = []
                
                skip_texts = {
                    "Search Results", "Sponsored result", "Sponsored",
                    "Hide sponsored result", "People also search for",
                    "About this page", "Feedback",
                }

                for el in all_els:
                    try:
                        bounds = el.info.get("bounds") or el.rect
                        text = el.attrib.get("text", "") or el.text
                        
                        # Must be below "Search Results"
                        el_top = bounds[1] if isinstance(bounds, (list, tuple)) else bounds.get("top", 0)
                        if el_top <= sr_bottom:
                            continue
                        
                        # Skip non-content texts
                        if not text or text.strip() in skip_texts:
                            continue
                        
                        candidates.append({
                            "top": el_top,
                            "text": text.strip(),
                            "element": el,
                        })
                    except Exception:
                        continue

                # Sort by vertical position (top to bottom)
                candidates.sort(key=lambda x: x["top"])

                # Tap index 1 (same as Maestro's index: 1)
                if len(candidates) > 1:
                    target = candidates[1]
                    target["element"].click()
                    time.sleep(4)
                    result_tapped = True
                    log("tapOn below \"Search Results\" index 1",
                        detail=f"text: {target['text'][:50]}")
                elif len(candidates) > 0:
                    target = candidates[0]
                    target["element"].click()
                    time.sleep(4)
                    result_tapped = True
                    log("tapOn below \"Search Results\" index 0",
                        detail=f"only one: {target['text'][:50]}")

            except Exception as e:
                log("tapOn below \"Search Results\"", status="warn",
                    detail=str(e))

        # Fallback: find by domain text
        if not result_tapped:
            domain = config["PLATFORM_DOMAIN"]
            for scroll in range(4):
                try:
                    el = d(textContains=domain)
                    if el.exists(timeout=2):
                        el.click()
                        time.sleep(4)
                        result_tapped = True
                        log("tapOn search result", detail=f"fallback domain: {domain}")
                        break
                except Exception:
                    pass
                d.swipe_ext("up", scale=0.4)
                time.sleep(1.5)

        if not result_tapped:
            screenshot(d, device_id, "no_result")
            log("tapOn search result", status="fail")
            return {"status": "error", "error": "Could not find platform in search results", "steps": steps}

        # ==============================================================
        # STEP 13: tapOn "No thanks" (optional)
        # ==============================================================
        for dismiss_text in ["No thanks", "No, thanks", "Dismiss", "Got it",
                             "Not now", "Try it", "Maybe later"]:
            try:
                el = d(text=dismiss_text)
                if el.exists(timeout=1.5):
                    el.click()
                    time.sleep(1)
                    log(f"tapOn (optional) \"{dismiss_text}\"")
            except Exception:
                pass

        # ==============================================================
        # STEP 14: waitForAnimationToEnd
        # ==============================================================
        wait_for_animation(d, 3)
        log("waitForAnimationToEnd")

        screenshot(d, device_id, "platform_loaded")

        # ==============================================================
        # STEP 15: inputText PROMPT
        # ==============================================================
        input_found = find_and_click(d, config["input_selectors"],
                                     timeout=8, label="prompt input")

        if not input_found:
            try:
                edits = d(className="android.widget.EditText")
                if edits.exists(timeout=5):
                    edits[-1].click()
                    time.sleep(0.5)
                    input_found = True
                    log("tapOn input field", detail="fallback EditText")
            except Exception:
                pass

        if not input_found:
            d.swipe_ext("up", scale=0.3)
            time.sleep(2)
            input_found = find_and_click(d, config["input_selectors"], timeout=5)
            if not input_found:
                screenshot(d, device_id, "no_input")
                log("inputText PROMPT", status="fail", detail="input field not found")
                return {"status": "error", "error": "Could not find prompt input field", "steps": steps}

        type_text(d, prompt)
        log(f"inputText PROMPT", detail=prompt[:60] + "...")

        # ==============================================================
        # STEP 16: tapOn submit button
        # ==============================================================
        submitted = find_and_click(d, config["submit_selectors"],
                                   timeout=5, label="submit")
        if not submitted:
            d.press("enter")
            log("tapOn submit", detail="fallback Enter key")
        else:
            log("tapOn submit")

        # ==============================================================
        # STEP 17: waitForAnimationToEnd
        # ==============================================================
        wait_for_animation(d, 3)
        log("waitForAnimationToEnd")

        # ==============================================================
        # STEP 18: scroll x5 (simulate reading the response)
        # ==============================================================
        for i in range(5):
            try:
                d.swipe_ext("up", scale=0.3)
                time.sleep(2)
            except Exception:
                pass
        log("scroll x5 (read response)")

        # ==============================================================
        # STEP 19: Follow-up (only if FOLLOW_UP is not empty)
        # ==============================================================
        if follow_up and follow_up.strip():
            log("Starting follow-up")

            followup_selectors = config.get("followup_input_selectors",
                                             config["input_selectors"])
            followup_found = find_and_click(d, followup_selectors,
                                            timeout=8, label="follow-up input")

            if not followup_found:
                try:
                    edits = d(className="android.widget.EditText")
                    if edits.exists(timeout=5):
                        edits[-1].click()
                        time.sleep(0.5)
                        followup_found = True
                except Exception:
                    pass

            if followup_found:
                type_text(d, follow_up)
                log(f"inputText FOLLOW_UP", detail=follow_up[:60] + "...")

                submitted = find_and_click(d, config["submit_selectors"], timeout=5)
                if not submitted:
                    d.press("enter")
                log("tapOn submit (follow-up)")

                wait_for_animation(d, 3)
                for i in range(5):
                    try:
                        d.swipe_ext("up", scale=0.3)
                        time.sleep(2)
                    except Exception:
                        pass
                log("scroll x5 (read follow-up response)")
            else:
                log("follow-up input not found", status="fail")

        # ==============================================================
        # DONE
        # ==============================================================
        screenshot(d, device_id, "final")
        log("Session complete")

        return {"status": "success", "steps": steps}

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        log("Unexpected error", status="fail", detail=error_msg)
        screenshot(d, device_id, "error")
        traceback.print_exc()
        return {"status": "error", "error": error_msg, "steps": steps}


# ── Factory ───────────────────────────────────────────────────────────────────

def create_flow(platform, device, device_id="unknown"):
    """
    Returns a flow object with a .run() method.
    Usage:
        flow = create_flow("Gemini", d, "device-001")
        result = flow.run(prompt="...", follow_up="...")
    """
    if platform not in PLATFORM_CONFIG:
        raise ValueError(f"Unknown platform: {platform}. "
                         f"Choose from: {list(PLATFORM_CONFIG.keys())}")

    class Flow:
        def __init__(self):
            self.platform = platform

        def run(self, prompt, follow_up=None, **kwargs):
            return run_aeo_flow(device, platform, prompt,
                                follow_up=follow_up, device_id=device_id)

    return Flow()


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uiautomator2 as u2
    import sys

    if len(sys.argv) < 3:
        print("Usage: python3 u2_flows.py <serial> <platform> [prompt] [follow_up]")
        print()
        print("Examples:")
        print("  python3 u2_flows.py 324651961440 Gemini")
        print("  python3 u2_flows.py 324651961440 Gemini 'best plumber in SF'")
        print("  python3 u2_flows.py 324651961440 ChatGPT 'best plumber' 'any reviews?'")
        print()
        print("Platforms: Gemini, ChatGPT, Perplexity")
        sys.exit(1)

    serial   = sys.argv[1]
    platform = sys.argv[2]
    prompt   = sys.argv[3] if len(sys.argv) > 3 else "What is the best coffee shop in San Francisco?"
    followup = sys.argv[4] if len(sys.argv) > 4 else None

    print(f"\nTesting {platform} flow on {serial}")
    print(f"   Prompt: {prompt}")
    if followup:
        print(f"   Follow-up: {followup}")
    print()

    d = u2.connect(serial)
    print(f"   Device: {d.info.get('productName', 'unknown')}")
    print()

    result = run_aeo_flow(d, platform, prompt, follow_up=followup, device_id=serial)

    print(f"\n{'='*60}")
    print(f"Result: {result['status']}")
    if result.get("error"):
        print(f"Error: {result['error']}")
    print(f"Steps ({len(result['steps'])}):")
    for s in result["steps"]:
        icon = "OK" if s["status"] == "ok" else "FAIL" if s["status"] == "fail" else "WARN"
        print(f"  [{icon}] {s['step']}" + (f" -- {s['detail']}" if s['detail'] else ""))