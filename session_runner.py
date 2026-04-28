"""
AEO Appium Session Runner — v1.0
----------------------------------
Manages per-device Appium sessions with TRUE parallel threading.
Each device gets its own Appium server (port read from session data or active_devices.json)
and its own driver instance. No shared state between device threads.

Ports are NOT hardcoded — they come from active_devices.json via the session dict.
"""

import subprocess
import threading
import time
import traceback
from typing import Callable, Dict, List, Optional, Any

from appium import webdriver
from appium.options.android.uiautomator2.base import UiAutomator2Options

import re

from flows import clear_chrome, run_flow
from flows_adb import run_flow_adb, clear_chrome as clear_chrome_adb
from proxy import setup_device, teardown_device


# ── Audit prompt + ranking extraction ─────────────────────────────────────────

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


def _build_audit_prompt(job: dict) -> str:
    return AUDIT_PROMPT_TEMPLATE.format(
        keyword=job.get("keyword_text", ""),
        city=job.get("city", ""),
        state=job.get("state", ""),
        biz_name=job.get("biz_name", ""),
        biz_url=job.get("biz_url", job.get("gmb_url", "")),
    )


def _extract_ranking(response_text: str, biz_name: str, biz_url: str = "") -> dict:
    if not response_text:
        return {"position": None, "total": None, "mentioned": False, "context": ""}
    text = response_text.strip()
    biz_lower = biz_name.lower()
    url_domain = ""
    if biz_url:
        url_domain = biz_url.lower().replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")
    text_lower = text.lower()
    mentioned = biz_lower in text_lower or (url_domain and url_domain in text_lower)

    for line in reversed(text.split("\n")):
        s = line.strip()
        if "e.g." in s.lower() or "example" in s.lower() or "where X" in s:
            continue
        m = re.search(r'\[RANK:\s*(\d+)\s*/\s*(\d+\+?)\]', s)
        if m:
            return {"position": int(m.group(1)), "total": m.group(2), "mentioned": True, "context": s[:200]}

    for line in text.split("\n"):
        if biz_lower in line.lower() and re.search(r'#\d+', line):
            m = re.search(r'#(\d+)\s*(?:out of|/)\s*(\d+\+?)', line)
            if m:
                return {"position": int(m.group(1)), "total": m.group(2), "mentioned": True, "context": line.strip()[:200]}

    return {"position": None, "total": None, "mentioned": mentioned, "context": ""}


# ── Per-device locks (prevents ADB collisions on the same device) ──────────────
# Created dynamically from whatever is in the active pool.
# Using a defaultdict-style approach so any device_id gets a lock on first use.

_locks_mutex = threading.Lock()
_device_locks: Dict[str, threading.Lock] = {}


def _get_lock(device_id: str) -> threading.Lock:
    with _locks_mutex:
        if device_id not in _device_locks:
            _device_locks[device_id] = threading.Lock()
        return _device_locks[device_id]


# ── Session Runner ─────────────────────────────────────────────────────────────

def _run_single_platform(serial: str, full_serial: str, port: int,
                         platform: str, prompt: str, follow_up: Optional[str],
                         device_id: str, use_adb: bool,
                         backlinks: List,
                         is_first: bool = True,
                         job_type: str = "daily",
                         audit_meta: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Run one platform flow on a specific device.
    Internal helper — does NOT manage proxy or locks.

    is_first: if True, clears Chrome and launches fresh.
              if False, Chrome is already open — just navigate to new platform.
    job_type: "daily" for seeding sessions, "audit" for ranking audit.
    audit_meta: dict with biz_name, biz_url, city, state — only needed for audit.
    """
    driver = None
    platform_start = time.time()

    # Build audit prompt if this is an audit job
    audit_result = None
    if job_type == "audit" and audit_meta:
        effective_prompt = _build_audit_prompt({
            "keyword_text": prompt,
            "city": audit_meta.get("city", ""),
            "state": audit_meta.get("state", ""),
            "biz_name": audit_meta.get("biz_name", ""),
            "biz_url": audit_meta.get("biz_url", audit_meta.get("gmb_url", "")),
        })
        follow_up = None  # audit has no follow-up
        backlinks = []    # audit doesn't click backlinks
    else:
        effective_prompt = prompt

    try:
        if use_adb:
            print(f"[{device_id}] ADB-only mode — {platform}" +
                  (" [audit]" if job_type == "audit" else ""))
            if is_first:
                clear_chrome_adb(full_serial)
                time.sleep(1)
                subprocess.run(
                    ["adb", "-s", full_serial, "shell", "am", "start", "-n",
                     "com.android.chrome/com.google.android.apps.chrome.Main"],
                    capture_output=True, timeout=10,
                )
                time.sleep(3)

            result = run_flow_adb(platform, full_serial, effective_prompt, follow_up, backlinks=backlinks)

        else:
            # Chrome-stays-open optimization: proxy.py's preflight already opened Chrome
            # to ifconfig.me and dismissed FRE. Skip pm clear + re-FRE — Appium will
            # attach to the existing Chrome session and navigate the same tab to the
            # platform URL. Saves ~10-15s and eliminates FRE-loop failures.
            if is_first:
                # Just lock orientation (no Chrome wipe)
                subprocess.run(
                    ["adb", "-s", full_serial, "shell", "settings", "put", "system", "accelerometer_rotation", "0"],
                    capture_output=True, timeout=5,
                )
                subprocess.run(
                    ["adb", "-s", full_serial, "shell", "settings", "put", "system", "user_rotation", "0"],
                    capture_output=True, timeout=5,
                )

            options = UiAutomator2Options()
            options.platform_name       = "Android"
            options.device_name         = serial
            options.udid                = full_serial
            options.automation_name     = "UiAutomator2"
            options.no_reset            = True  # attach to existing Chrome from preflight
            options.new_command_timeout = 300
            options.orientation         = "PORTRAIT"
            options.app_package         = "com.android.chrome"
            options.app_activity        = "com.google.android.apps.chrome.Main"
            options.set_capability("appium:chromeOptions", {"args": []})
            options.set_capability("appium:chromedriverAutodownload", True)

            appium_url = f"http://localhost:{port}/wd/hub"
            print(f"[{device_id}] Connecting to Appium at {appium_url} ({platform})...")

            driver = webdriver.Remote(appium_url, options=options)
            driver.implicitly_wait(5)

            result = run_flow(platform, driver, full_serial, effective_prompt, follow_up, backlinks=backlinks)

        success = result.get("status") == "success"
        duration = round(time.time() - platform_start, 1)
        error = result.get("error", "")

        # Extract ranking for audit jobs
        if job_type == "audit" and audit_meta and success:
            response_text = result.get("response_preview", "")
            audit_result = _extract_ranking(
                response_text,
                audit_meta.get("biz_name", ""),
                audit_meta.get("biz_url", audit_meta.get("gmb_url", "")),
            )

        print(f"[{device_id}] {platform} {'SUCCESS' if success else 'FAILED'} — {duration}s{' — ' + error if error else ''}" +
              (f" rank={audit_result.get('position')}/{audit_result.get('total')}" if audit_result and audit_result.get('position') else ""))
        ret = {
            "success":          success,
            "device_id":        device_id,
            "platform":         platform,
            "duration_s":       duration,
            "error":            error,
            "steps":            result.get("steps", []),
            "response_preview": result.get("response_preview", ""),
        }
        if audit_result:
            ret["audit"] = audit_result
        return ret

    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        duration = round(time.time() - platform_start, 1)
        print(f"[{device_id}] {platform} ERROR — {err}")
        traceback.print_exc()
        return {
            "success":    False,
            "device_id":  device_id,
            "platform":   platform,
            "duration_s": duration,
            "error":      err,
            "steps":      [],
        }

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def run_session(serial: str, full_serial: str, port: int,
                platform: str, prompt: str, follow_up: Optional[str],
                device_id: str, sess_meta: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Run one AEO session on a specific device.

    If sess_meta contains "platforms" (list), runs all platforms sequentially
    under the same proxy session. Otherwise runs a single platform.

    Proxy is connected before platforms and disconnected after all complete.

    Returns:
        {"success": True/False, "device_id": ..., "output": ..., "steps": [...],
         "proxy": {...}, "platform_results": [...]}
    """
    lock    = _get_lock(device_id)
    meta    = sess_meta if isinstance(sess_meta, dict) else {}
    use_adb   = meta.get("use_adb", False)
    backlinks = meta.get("backlinks", [])
    job_type  = meta.get("type", "daily")
    audit_meta = meta.get("audit_meta") or {}
    proxy_config = meta.get("proxy", None)
    if proxy_config:
        from proxy import enrich_proxy_config
        proxy_config = enrich_proxy_config(proxy_config, meta)
    platforms = meta.get("platforms", [platform])

    lock.acquire()
    proxy_info = None

    try:
        # ── Setup device: proxy + location + timezone ──
        if proxy_config:
            print(f"[{device_id}] Setting up device (proxy + location + timezone)...")
            device_setup = setup_device(full_serial, proxy_config)
            proxy_info = device_setup.get("proxy", {})
            # Enrich proxy_info with location + timezone so log_session can record
            # the randomized GPS and TZ that were mocked for this session.
            loc = device_setup.get("location") or {}
            loc_resp = loc.get("response") or {}
            tz = device_setup.get("timezone") or {}
            tz_resp = tz.get("response") or {}
            proxy_info["mocked_latitude"]  = loc_resp.get("latitude")
            proxy_info["mocked_longitude"] = loc_resp.get("longitude")
            proxy_info["mocked_timezone"]  = tz_resp.get("timezone")
            proxy_info["base_latitude"]    = proxy_config.get("latitude")
            proxy_info["base_longitude"]   = proxy_config.get("longitude")
            if proxy_info.get("status") == "ERROR":
                # Preflight failed — don't waste time running the AI flow
                print(f"[{device_id}] Preflight failed — skipping AI flow: {proxy_info.get('error')}")
                return {
                    "success":          False,
                    "device_id":        device_id,
                    "output":           "preflight_failed",
                    "steps":            ["preflight_failed"],
                    "proxy":            proxy_info,
                    "platform_results": [{
                        "success":   False,
                        "device_id": device_id,
                        "platform":  platforms[0] if platforms else platform,
                        "duration_s": 0,
                        "error":     proxy_info.get("error", "preflight_failed"),
                        "steps":     ["preflight_failed"],
                    }],
                }
            if proxy_info.get("status") != "CONNECTED":
                print(f"[{device_id}] Proxy connection failed — continuing without proxy")
            else:
                time.sleep(5)  # Let VPN stabilize

        # ── Run all platforms sequentially under same proxy ──
        platform_results = []
        all_steps = []

        for i, plat in enumerate(platforms):
            print(f"\n[{device_id}] ── {plat} ──")
            result = _run_single_platform(
                serial=serial, full_serial=full_serial, port=port,
                platform=plat, prompt=prompt, follow_up=follow_up,
                device_id=device_id, use_adb=use_adb, backlinks=backlinks,
                is_first=(i == 0),
                job_type=job_type,
                audit_meta=audit_meta,
            )
            platform_results.append(result)
            all_steps.extend(result.get("steps", []))

            # Wait between platforms
            if plat != platforms[-1]:
                print(f"[{device_id}] Waiting 3s before next platform...")
                time.sleep(3)

        overall_success = all(r.get("success") for r in platform_results)
        output = f"platforms={len(platforms)} passed={sum(1 for r in platform_results if r.get('success'))}"

        # Flatten the most useful info from per-platform results for the
        # scheduler/caller. See docs/EXECUTOR_PAYLOAD.md for the full contract.
        first_error = next(
            (r.get("error", "") for r in platform_results if not r.get("success")),
            "",
        )
        clicked_step = next(
            (s for s in all_steps if isinstance(s, str) and s.startswith("backlink_clicked:")),
            None,
        )
        backlink_clicked = clicked_step.split(":", 1)[1] if clicked_step else None
        response_preview = next(
            (r.get("response_preview", "") for r in platform_results if r.get("response_preview")),
            "",
        )

        # Collect audit results from platform runs
        audit_rankings = {}
        for r in platform_results:
            if r.get("audit"):
                audit_rankings[r["platform"]] = r["audit"]

        print(f"[{device_id}] {'ALL PASSED' if overall_success else 'SOME FAILED'} — {output}")
        ret = {
            "success":          overall_success,
            "device_id":        device_id,
            "output":           output,
            "error":            first_error,
            "steps":            all_steps,
            "proxy":            proxy_info,
            "platform_results": platform_results,
            "backlink_clicked": backlink_clicked,
            "response_preview": response_preview,
        }
        if audit_rankings:
            ret["audit"] = audit_rankings
        return ret

    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(f"[{device_id}] ERROR — {err}")
        traceback.print_exc()
        return {
            "success":   False,
            "device_id": device_id,
            "output":    err,
            "steps":     [],
            "proxy":     proxy_info,
        }

    finally:
        # ── Teardown: disconnect proxy ──
        if proxy_config:
            try:
                teardown_device(full_serial)
            except Exception as e:
                print(f"[{device_id}] Teardown error: {e}")

        lock.release()
        print(f"[{device_id}] Session done, lock released.")


# ── Sequential Runner ──────────────────────────────────────────────────────────

def run_sequential(sessions: List[Dict], on_complete: Optional[Callable] = None) -> List[Dict]:
    """
    Run sessions one-at-a-time. Each device connects VPN, runs flow, disconnects
    before the next device starts. More observable and reliable than parallel,
    at the cost of total wall time.
    """
    results: List[Dict] = []
    for i, sess in enumerate(sessions, 1):
        port    = sess.get("port")
        use_adb = sess.get("use_adb", False)
        print(f"\n{'='*60}\n[{i}/{len(sessions)}] {sess['device_id']} — {sess['platform']}\n{'='*60}")

        if not port and not use_adb:
            result = {
                "success":   False,
                "device_id": sess["device_id"],
                "output":    f"No Appium port in session for {sess['device_id']}",
                "steps":     [],
            }
        else:
            result = run_session(
                serial      = sess["serial"],
                full_serial = sess.get("full_serial", sess["serial"]),
                port        = port or 0,
                platform    = sess["platform"],
                prompt      = sess["prompt"],
                follow_up   = sess.get("follow_up"),
                device_id   = sess["device_id"],
                sess_meta   = {
                    "use_adb":   use_adb,
                    "backlinks": sess.get("backlinks", []),
                    "proxy":     sess.get("proxy"),
                    "platforms": sess.get("platforms", [sess["platform"]]),
                    "type":      sess.get("type", "daily"),
                    "audit_meta": sess.get("audit_meta"),
                },
            )

        results.append(result)
        if on_complete:
            try:
                on_complete(sess, result)
            except Exception as e:
                print(f"[{sess['device_id']}] on_complete error: {e}")

    return results


# ── Parallel Runner ────────────────────────────────────────────────────────────

def run_parallel(sessions: List[Dict], on_complete: Optional[Callable] = None) -> List[Dict]:
    """
    Run all sessions simultaneously — one thread per device.

    Each session dict must contain:
      device_id, serial, full_serial, port, platform, prompt, follow_up

    on_complete(sess, result) is called immediately after each session finishes.
    Returns list of result dicts.
    """
    results      = []
    results_lock = threading.Lock()
    threads      = []

    def worker(sess):
        port    = sess.get("port")
        use_adb = sess.get("use_adb", False)

        if not port and not use_adb:
            result = {
                "success":   False,
                "device_id": sess["device_id"],
                "output":    f"No Appium port in session for {sess['device_id']}",
                "steps":     [],
            }
        else:
            result = run_session(
                serial      = sess["serial"],
                full_serial = sess.get("full_serial", sess["serial"]),
                port        = port or 0,
                platform    = sess["platform"],
                prompt      = sess["prompt"],
                follow_up   = sess.get("follow_up"),
                device_id   = sess["device_id"],
                sess_meta   = {
                    "use_adb": use_adb,
                    "backlinks": sess.get("backlinks", []),
                    "proxy": sess.get("proxy"),
                    "platforms": sess.get("platforms", [sess["platform"]]),
                },
            )

        with results_lock:
            results.append(result)

        if on_complete:
            try:
                on_complete(sess, result)
            except Exception as e:
                print(f"[{sess['device_id']}] on_complete error: {e}")

    # Stagger thread starts by ~4s. DM's adb-proxy socat bridge can't cleanly
    # serve N simultaneous `am start` / `appops set` commands — responses get
    # tangled and some phones end up with half-started SocksDroid (tun0 exists
    # but no routing). A small gap lets each phone's setup finish DM work
    # before the next one begins, then overlap fully on the AI flow.
    STAGGER_SECONDS = 4
    for i, sess in enumerate(sessions):
        if i > 0:
            time.sleep(STAGGER_SECONDS)
        t = threading.Thread(target=worker, args=(sess,), daemon=True)
        t.start()
        threads.append(t)
        print(f"Thread started for {sess['device_id']} (port {sess.get('port')})")

    for t in threads:
        t.join()

    return results
