"""
Test 2: Chrome DevTools Protocol full-page screenshot
Connects to Chrome's remote debugging, takes a full-page screenshot in one shot.
"""
import subprocess
import time
import os
import json
import base64

SERIAL = "adb-R83L103VCVH-uvv2pp._adb-tls-connect._tcp"
OUTPUT_DIR = "screenshot_test"
LOCAL_PORT = 9222
os.makedirs(OUTPUT_DIR, exist_ok=True)


def adb(*args, timeout=10):
    cmd = ["adb", "-s", SERIAL] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout


def setup_cdp():
    """Forward Chrome DevTools port and find the WebSocket URL."""
    # Forward port
    subprocess.run(
        ["adb", "-s", SERIAL, "forward", f"tcp:{LOCAL_PORT}", "localabstract:chrome_devtools_remote"],
        capture_output=True
    )
    time.sleep(1)

    # Get list of debuggable pages
    import urllib.request
    try:
        resp = urllib.request.urlopen(f"http://localhost:{LOCAL_PORT}/json")
        pages = json.loads(resp.read())
        if pages:
            print(f"Found {len(pages)} debuggable page(s)")
            for p in pages:
                print(f"  - {p.get('title', 'untitled')}: {p.get('url', '')}")
            return pages[0].get("webSocketDebuggerUrl")
        else:
            print("No debuggable pages found")
            return None
    except Exception as e:
        print(f"Failed to connect to CDP: {e}")
        return None


def cdp_full_screenshot(ws_url):
    """Take a full-page screenshot via CDP WebSocket."""
    import websocket

    ws = websocket.create_connection(ws_url, suppress_origin=True)

    # Get page layout metrics to know full page size
    ws.send(json.dumps({"id": 1, "method": "Page.getLayoutMetrics"}))
    metrics = json.loads(ws.recv())
    content_size = metrics["result"]["contentSize"]
    print(f"Page size: {content_size['width']}x{content_size['height']}")

    # Take full-page screenshot
    ws.send(json.dumps({
        "id": 2,
        "method": "Page.captureScreenshot",
        "params": {
            "format": "png",
            "captureBeyondViewport": True,
            "clip": {
                "x": 0,
                "y": 0,
                "width": content_size["width"],
                "height": content_size["height"],
                "scale": 1
            }
        }
    }))
    result = json.loads(ws.recv())
    ws.close()

    if "result" in result and "data" in result["result"]:
        return base64.b64decode(result["result"]["data"])
    else:
        print(f"CDP error: {result}")
        return None


def main():
    print(f"Device: {SERIAL}")
    print("Setting up Chrome DevTools Protocol...")

    ws_url = setup_cdp()
    if not ws_url:
        print("\nFailed to get WebSocket URL.")
        print("Make sure Chrome is open with a page loaded on the device.")
        print("Try: adb -s <serial> shell am start -n com.android.chrome/com.google.android.apps.chrome.Main")
        return

    print(f"\nWebSocket: {ws_url}")

    try:
        print("Taking full-page screenshot via CDP...")
        img_data = cdp_full_screenshot(ws_url)
        if img_data:
            output_path = os.path.join(OUTPUT_DIR, "cdp_fullpage.png")
            with open(output_path, "wb") as f:
                f.write(img_data)
            print(f"Saved: {output_path}")
            print(f"Size: {len(img_data)} bytes")
        else:
            print("Failed to capture screenshot")
    except Exception as e:
        print(f"Error: {e}")
        print("\nYou may need: pip3 install websocket-client")
    finally:
        # Clean up port forward
        subprocess.run(
            ["adb", "-s", SERIAL, "forward", "--remove", f"tcp:{LOCAL_PORT}"],
            capture_output=True
        )


if __name__ == "__main__":
    main()
