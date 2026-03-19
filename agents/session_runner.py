"""
Session Runner — executes AEO sessions on physical Android devices via DroidRun.
Each device gets its own AdbTools + DroidAgent — truly parallel via asyncio.gather.
"""

import os
import asyncio
import subprocess
from droidrun import DroidAgent, DroidrunConfig, AndroidDriver, DeviceConfig


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "config.yaml")

PLATFORM_NAMES = {
    "Gemini":     "Google Gemini",
    "ChatGPT":    "ChatGPT",
    "Perplexity": "Perplexity AI",
}
PLATFORM_DOMAINS = {
    "Gemini":     "gemini.google.com",
    "ChatGPT":    "chatgpt.com",
    "Perplexity": "www.perplexity.ai",
}


def build_session_goal(platform, prompt, follow_up):
    """
    Build a natural language goal for the DroidRun agent.
    Mimics real human behavior: Google the platform name, click organic result,
    then interact with the AI chat.
    """
    name   = PLATFORM_NAMES.get(platform, "Google Gemini")
    domain = PLATFORM_DOMAINS.get(platform, "gemini.google.com")

    goal = (
        f"Launch the Chrome browser app using package name 'com.android.chrome'. "
        f"Once Chrome is open, tap the address bar at the top and type '{name}' then press Enter. "
        f"Chrome will search Google automatically. "
        f"IMPORTANT: If you see a CAPTCHA page ('Our systems have detected unusual traffic', "
        f"'I'm not a robot', or any image verification challenge), do NOT try to solve it and do NOT press the HOME button. "
        f"Instead, tap the Chrome address bar, type '{name}' again and press Enter to retry. "
        f"You are now on Google search results. "
        f"IMPORTANT: Do NOT tap on any sponsored or ad results at the top. "
        f"Scroll down past any ads and find the organic (non-sponsored) search result "
        f"that shows '{domain}' in the URL. Tap on that result. "
        f"Wait for the {name} page to fully load. "
        f"IMPORTANT: If you see ANY popup, banner, or overlay blocking the page: "
        f"- 'Get Comet for your phone' or 'Install Comet': tap OUTSIDE the popup (tap on a dark/dimmed area around it) to dismiss. "
        f"- 'Open in App': IGNORE it completely, do NOT tap it, do NOT press back. Just scroll past it or tap the input field directly. "
        f"- 'Chat with Gemini in a app' or 'Try app': IGNORE it, just scroll down to find the input field. "
        f"- 'Sign up', 'Log in': IGNORE it, find the input field and type directly. "
        f"- Any other popup: tap outside it or scroll past it. Do NOT press the Android BACK button for popups — it will navigate away from the page. "
        f"Find the text input field or chat box on the page. "
        f"Tap on it to focus it, then type exactly this message: "
        f"'{prompt}' "
        f"Press Enter or tap the send button to submit the message. "
        f"Wait for the AI response to finish loading. "
        f"Then scroll through the entire response to read it fully. "
        f"Use BIG swipes: swipe from coordinate [360, 1200] to [360, 300] with duration 0.8 seconds. "
        f"After each swipe, wait 1.5 seconds, then check if the page moved. "
        f"If the page did not move, you have reached the bottom — stop scrolling. "
        f"If the page is still moving, keep swiping until it stops. "
        f"You know you are done when a swipe produces no movement. "
        f"Do NOT mark the task as complete until you have confirmed the page cannot scroll further."
    )

    if follow_up:
        goal += (
            f" After you have scrolled to the very bottom of the first response, "
            f"find the text input field again, "
            f"tap on it, and type exactly this follow-up message: "
            f"'{follow_up}' "
            f"Press Enter or tap the send button. "
            f"Wait for the second response to finish loading. "
            f"Then scroll through the entire second response the same way — "
            f"swipe from [360, 1200] to [360, 300], wait 1.5 seconds after each swipe, "
            f"and stop when the page no longer moves."
        )

    return goal


def clear_chrome(serial):
    """Clear Chrome app data before session to avoid CAPTCHA and stale state."""
    try:
        result = subprocess.run(
            ["adb", "-s", serial, "shell", "pm", "clear", "com.android.chrome"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            print(f"🧹 Chrome cleared on {serial}")
        else:
            print(f"⚠️  Chrome clear failed on {serial}: {result.stderr.strip()}")
    except Exception as e:
        print(f"⚠️  Chrome clear error on {serial}: {e}")


async def run_session(serial, platform, prompt, follow_up, device_id="unknown"):
    """
    Run a single AEO session on one device.
    Clears Chrome first to avoid CAPTCHA, then runs DroidRun agent.
    Returns dict with success status and details.
    """
    print(f"\n{'='*60}")
    print(f"📱 Device   : {device_id} ({serial})")
    print(f"🎯 Platform : {platform}")
    print(f"📝 Prompt   : {prompt[:80]}...")
    if follow_up:
        print(f"💬 Follow-up: {follow_up[:60]}...")
    print(f"{'='*60}")

    # Clear Chrome data before each session — fresh start, no CAPTCHA
    clear_chrome(serial)

    try:
        config = DroidrunConfig.from_yaml(CONFIG_PATH)
        config.device = DeviceConfig(serial=serial, platform="android")

        goal = build_session_goal(platform, prompt, follow_up)

        print(f"▶️  DroidRun agent starting on {device_id} ({serial})")

        agent = DroidAgent(
            goal=goal,
            config=config,
            timeout=600,
        )

        result = await agent.run()

        success = getattr(result, "success", False)
        output = getattr(result, "reason", str(result))

        if success:
            print(f"✅ Session complete on {device_id}")
        else:
            print(f"❌ DroidRun failed on {device_id}: {output}")

        return {
            "success": success,
            "device_id": device_id,
            "output": output,
        }

    except Exception as e:
        print(f"❌ Error on {device_id}: {e}")
        return {
            "success": False,
            "device_id": device_id,
            "output": str(e),
        }


async def run_parallel(sessions, stagger_seconds=5):
    """
    Run sessions so that:
      - Each device's sessions execute SEQUENTIALLY (one at a time per device)
      - All devices run CONCURRENTLY in parallel
      - Devices start staggered to avoid simultaneous Google searches
    Returns results in the same order as the input sessions list.
    """
    from collections import defaultdict

    # Group sessions by device, preserving assignment order
    device_queues = defaultdict(list)
    for s in sessions:
        device_queues[s["device_id"]].append(s)

    device_ids = list(device_queues.keys())

    async def run_device_queue(device_id, queue, start_delay):
        """Run all sessions for one device sequentially after an initial delay."""
        if start_delay > 0:
            await asyncio.sleep(start_delay)
        results = []
        for sess in queue:
            result = await run_session(
                serial=sess["serial"],
                platform=sess["platform"],
                prompt=sess["prompt"],
                follow_up=sess["follow_up"],
                device_id=sess.get("device_id", "unknown"),
            )
            results.append(result)
        return results

    # Launch all device queues in parallel, each starting stagger_seconds apart
    tasks = [
        run_device_queue(device_id, device_queues[device_id], i * stagger_seconds)
        for i, device_id in enumerate(device_ids)
    ]
    grouped = await asyncio.gather(*tasks, return_exceptions=True)

    # Build per-device result lists
    device_results = {}
    for i, device_id in enumerate(device_ids):
        group = grouped[i]
        if isinstance(group, Exception):
            device_results[device_id] = [
                {"success": False, "device_id": device_id, "output": str(group)}
                for _ in device_queues[device_id]
            ]
        else:
            device_results[device_id] = group

    # Return results in original session order
    device_cursor = {d: 0 for d in device_ids}
    final = []
    for sess in sessions:
        d = sess["device_id"]
        idx = device_cursor[d]
        results_for_device = device_results.get(d, [])
        if idx < len(results_for_device):
            final.append(results_for_device[idx])
        else:
            final.append({"success": False, "device_id": d, "output": "result missing"})
        device_cursor[d] += 1

    return final
