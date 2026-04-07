"""
Proxy Speed Test — measures page load times with and without proxy.
Usage:
  python3 test_proxy_speed.py                    # auto-detect device
  python3 test_proxy_speed.py --serial <serial>  # specific device
"""

import argparse
import json
import subprocess
import time

from proxy import connect_proxy, disconnect_proxy
from flows_adb import wait_for_page_ready, dump_ui, find_and_tap, adb, clear_chrome, dismiss_chrome_fre

TEST_URLS = [
    ("Gemini", "https://gemini.google.com", "gemini"),
    ("ChatGPT", "https://chatgpt.com", "chatgpt"),
    ("Perplexity", "https://www.perplexity.ai", "perplexity"),
]

PROXY_CONFIG = {
    "session_duration": 60,
    "country": "us",
    "zip": "94117",
}


def get_first_device():
    result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
    for line in result.stdout.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def measure_page_load(serial, url, platform, timeout=60):
    """Navigate to URL and measure time until real content is visible.
    Uses wait_for_page_ready which checks for actual UI elements like 'Ask Gemini', 'Ask anything'."""
    subprocess.run(
        ["adb", "-s", serial, "shell", "am", "start", "-a",
         "android.intent.action.VIEW", "-d", url, "com.android.chrome"],
        capture_output=True, timeout=10,
    )
    start = time.time()
    ok = wait_for_page_ready(serial, platform=platform, max_wait=timeout, url=url)
    elapsed = round(time.time() - start, 1)
    return elapsed if ok else None


def run_test(serial, label, urls, is_first=False):
    print(f"\n{'='*50}")
    print(f"  {label}")
    print(f"{'='*50}")

    if is_first:
        print("  Clearing Chrome...")
        clear_chrome(serial)
        time.sleep(2)
        adb(serial, "shell", "am", "start", "-n",
            "com.android.chrome/com.google.android.apps.chrome.Main")
        time.sleep(3)
        dismiss_chrome_fre(serial)
        time.sleep(2)

    results = []
    for name, url, platform in urls:
        print(f"  {name:12s} → loading...", end=" ", flush=True)
        elapsed = measure_page_load(serial, url, platform)
        status = f"{elapsed}s" if elapsed else "TIMEOUT"
        results.append((name, elapsed))
        print(status)
        time.sleep(3)
    return results


def main():
    parser = argparse.ArgumentParser(description="Proxy Speed Test")
    parser.add_argument("--serial", help="Device serial")
    args = parser.parse_args()

    serial = args.serial or get_first_device()
    if not serial:
        print("No device found")
        return

    print(f"Device: {serial[:40]}")
    print(f"Testing {len(TEST_URLS)} URLs with and without proxy\n")

    # Test 1: No proxy
    disconnect_proxy(serial)
    time.sleep(2)
    no_proxy = run_test(serial, "WITHOUT PROXY (direct)", TEST_URLS, is_first=True)

    # Test 2: With proxy
    print("\nConnecting proxy...")
    result = connect_proxy(serial, PROXY_CONFIG)
    if result.get("status") != "CONNECTED":
        print(f"Proxy failed: {result}")
        return
    time.sleep(8)
    print("Proxy connected — waiting for VPN to stabilize...")
    with_proxy = run_test(serial, "WITH PROXY (us.decodo.com, 60s session)", TEST_URLS, is_first=True)

    # Cleanup
    disconnect_proxy(serial)

    # Summary
    print(f"\n{'='*50}")
    print(f"  SUMMARY")
    print(f"{'='*50}")
    print(f"  {'Site':12s} {'No Proxy':>10s} {'With Proxy':>12s} {'Diff':>8s}")
    print(f"  {'-'*44}")
    for i, (name, _, _) in enumerate(TEST_URLS):
        np = no_proxy[i][1]
        wp = with_proxy[i][1]
        np_str = f"{np}s" if np else "FAIL"
        wp_str = f"{wp}s" if wp else "FAIL"
        if np and wp:
            diff = f"+{round(wp - np, 1)}s"
        else:
            diff = "N/A"
        print(f"  {name:12s} {np_str:>10s} {wp_str:>12s} {diff:>8s}")


if __name__ == "__main__":
    main()
