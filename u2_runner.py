"""
AEO u2 Runner — v5.0
--------------------------
Replaces Maestro with uiautomator2 for TRUE parallel multi-device execution.
Each device gets its own independent u2 connection — no JVM, no port conflicts.

- Multi-client support via clients.json
- Static device assignments via device_assignments.json
- Rotation by KEYWORD: same keyword never runs twice per day (any device)
- /run-all endpoint: runs all sessions in PARALLEL across devices
- Platform randomization per session
- Platform-specific flows via u2_flows.py
- Full combined session logging
- Screenshots on failure for debugging

Setup (one-time):
    pip install uiautomator2 flask
    python -m uiautomator2 init --serial <SERIAL_1>
    python -m uiautomator2 init --serial <SERIAL_2>
"""


import json
import os
import random
import threading
import subprocess
import time
import concurrent.futures
from datetime import datetime, date, timezone
from flask import Flask, request, jsonify

import uiautomator2 as u2
from u2_flows import create_flow


app = Flask(__name__)


LOG_FILE         = "sessions_log.json"
CLIENTS_FILE     = "clients.json"
ASSIGNMENTS_FILE = "device_assignments.json"
ROTATION_FILE    = "device_rotation.json"


# ── Platforms ─────────────────────────────────────────────────────────────────

PLATFORMS = ["Gemini", "ChatGPT", "Perplexity"]


# ── Device Pool ───────────────────────────────────────────────────────────────
# No more MAESTRO_PORT — u2 handles connections independently per serial
DEVICE_POOL = {
    "device-001": {"serial": "324651961440"},
    "device-002": {"serial": "0B64C27G23101E10"},
}


# ── Proxy Pool ────────────────────────────────────────────────────────────────
PROXY_POOL = [
    "none:0",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def utcnow_iso():
    """Timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sanitize_for_input(text):
    """Strip non-ASCII characters that mobile keyboards can't type."""
    if not text:
        return text
    replacements = {
        "\u2014": "-", "\u2013": "-",
        "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"',
        "\u2026": "...",
        "\u00e9": "e", "\u00e8": "e", "\u00f1": "n",
        "\u00a0": " ", "\u200b": "",
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    return text.encode("ascii", "replace").decode("ascii").replace("?", "")


def check_device(serial):
    """Quick ADB check that device is connected."""
    try:
        r = subprocess.run(
            ["adb", "-s", serial, "shell", "echo", "ready"],
            capture_output=True, text=True, timeout=10
        )
        return r.returncode == 0
    except Exception:
        return False


# ── Clients ───────────────────────────────────────────────────────────────────

def load_clients():
    try:
        with open(CLIENTS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"  Could not load {CLIENTS_FILE}: {e}")
        return []


def get_client(client_id):
    clients = load_clients()
    try:
        return next((c for c in clients if c["id"] == int(client_id)), None)
    except:
        return None


# ── Device Assignments ────────────────────────────────────────────────────────

def load_assignments():
    try:
        with open(ASSIGNMENTS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"  Could not load {ASSIGNMENTS_FILE}: {e}")
        return {}


# ── Device Rotation ───────────────────────────────────────────────────────────

def load_rotation():
    try:
        with open(ROTATION_FILE, "r") as f:
            return json.load(f)
    except:
        return {}


def save_rotation(data):
    with open(ROTATION_FILE, "w") as f:
        json.dump(data, f, indent=2)


# Thread lock for rotation file writes (prevents corruption)
_rotation_lock = threading.Lock()


def init_today_rotation():
    today    = str(date.today())
    rotation = load_rotation()
    if today not in rotation:
        rotation[today] = {d: {} for d in DEVICE_POOL}
        save_rotation(rotation)
    return rotation


def is_keyword_done_today(client_id, keyword):
    today    = str(date.today())
    rotation = init_today_rotation()
    rot_key  = f"{client_id}:{keyword}"
    for device_id in DEVICE_POOL:
        device_today = rotation[today].get(device_id, {})
        if rot_key in device_today:
            return True
    return False


def is_device_used_today(device_id):
    today    = str(date.today())
    rotation = init_today_rotation()
    device_today = rotation[today].get(device_id, {})
    return len(device_today) > 0


def get_available_device(client_id, keyword):
    today       = str(date.today())
    rotation    = init_today_rotation()
    assignments = load_assignments()

    if is_keyword_done_today(client_id, keyword):
        print(f"     Keyword '{keyword}' for client {client_id} already done today")
        return None, None

    for device_id, dev_info in DEVICE_POOL.items():
        assigned_clients = assignments.get(device_id, [])
        if client_id not in assigned_clients:
            continue
        if is_device_used_today(device_id):
            print(f"     {device_id} already used today")
            continue
        return device_id, dev_info

    return None, None


def mark_device_used(device_id, client_id, keyword, platform, status="started"):
    today    = str(date.today())
    rot_key  = f"{client_id}:{keyword}"
    with _rotation_lock:
        rotation = load_rotation()
        if today not in rotation:
            rotation[today] = {d: {} for d in DEVICE_POOL}
        if device_id not in rotation[today]:
            rotation[today][device_id] = {}
        rotation[today][device_id][rot_key] = {
            "client_id": client_id,
            "keyword":   keyword,
            "platform":  platform,
            "status":    status,
            "timestamp": utcnow_iso()
        }
        save_rotation(rotation)


def update_device_status(device_id, client_id, keyword, status):
    today    = str(date.today())
    rot_key  = f"{client_id}:{keyword}"
    with _rotation_lock:
        try:
            rotation = load_rotation()
            rotation[today][device_id][rot_key]["status"] = status
            rotation[today][device_id][rot_key]["completed_at"] = utcnow_iso()
            save_rotation(rotation)
        except:
            pass


def get_rotation_status():
    today    = str(date.today())
    rotation = load_rotation()
    return rotation.get(today, {d: {} for d in DEVICE_POOL})


# ── Logging ───────────────────────────────────────────────────────────────────

# Thread lock for log file writes
_log_lock = threading.Lock()


def log_session(client, keyword, platform, prompt, follow_up,
                device_id, proxy, status, steps=None):
    entry = {
        "timestamp":    utcnow_iso(),
        "date":         str(date.today()),
        "client_id":    client["id"],
        "client_name":  client["biz_name"],
        "client_plan":  client["plan"],
        "client_city":  client["city"],
        "client_state": client["state"],
        "keyword":      keyword,
        "platform":     platform,
        "prompt":       prompt,
        "follow_up":    follow_up,
        "has_follow_up": follow_up is not None and follow_up != "",
        "device_id":    device_id,
        "proxy":        proxy if proxy == "none:0" else proxy.split(":")[0] + ":" + proxy.split(":")[1],
        "status":       status,
        "runner":       "uiautomator2",
        "steps_count":  len(steps) if steps else 0,
    }

    with _log_lock:
        try:
            with open(LOG_FILE, "r") as f:
                logs = json.load(f)
        except:
            logs = []
        logs.append(entry)
        with open(LOG_FILE, "w") as f:
            json.dump(logs, f, indent=2)

    print(f"   Logged -> {LOG_FILE} [{status}]")


# ── u2 Session Runner ────────────────────────────────────────────────────────

def run_u2_session(client, prompt, follow_up, platform,
                   keyword, device_id, dev_info, proxy):
    """
    Run a single AEO session using uiautomator2.
    This is safe to call from multiple threads simultaneously —
    each device gets its own independent u2 connection.
    """

    serial = dev_info["serial"]

    # Sanitize prompts
    prompt    = sanitize_for_input(prompt)
    follow_up = sanitize_for_input(follow_up) if follow_up else ""

    print(f"\n{'='*60}")
    print(f"   Client   : {client['biz_name']} (ID: {client['id']})")
    print(f"   Platform : {platform}")
    print(f"   Keyword  : {keyword}")
    print(f"   Device   : {device_id} ({serial})")
    print(f"   Prompt   : {prompt[:80]}...")
    if follow_up:
        print(f"   Follow-up: {follow_up[:60]}...")
    print(f"{'='*60}")

    status = "success"
    steps  = []

    try:
        # ── Connect to device ─────────────────────────────────────────
        print(f"   Connecting to {serial}...")
        d = u2.connect(serial)
        d.implicitly_wait(5)  # Default wait for element finds

        info = d.info
        print(f"   Connected: {info.get('productName', 'unknown')} "
              f"({info.get('screenSize', {}).get('x', '?')}x"
              f"{info.get('screenSize', {}).get('y', '?')})")

        # ── Run the platform flow ─────────────────────────────────────
        flow = create_flow(platform, d, device_id=device_id)

        print(f"   Running {platform} flow...")
        result = flow.run(
            prompt=prompt,
            follow_up=follow_up if follow_up.strip() else None,
        )

        status = result["status"]
        steps  = result.get("steps", [])

        if status == "error":
            print(f"   Flow error: {result.get('error', 'unknown')}")
        else:
            print(f"   Flow completed successfully ({len(steps)} steps)")

    except Exception as e:
        print(f"   Unexpected error: {e}")
        status = "error"
        import traceback
        traceback.print_exc()

    # ── Update rotation + log ─────────────────────────────────────────
    update_device_status(device_id, client["id"], keyword, status)
    log_session(client, keyword, platform, prompt,
                follow_up, device_id, proxy, status, steps)

    return status


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/run-aeo", methods=["POST"])
def run_aeo():
    data = request.get_json()
    if not data or "prompt" not in data:
        return jsonify({"error": "Missing prompt"}), 400

    client_id = int(data.get("client_id", 0))
    client    = get_client(client_id)
    if not client:
        return jsonify({"error": f"Client {client_id} not found"}), 400

    prompt    = data.get("prompt")
    follow_up = data.get("follow_up", None)
    keyword   = data.get("keyword", "unknown")

    device_id, dev_info = get_available_device(client_id, keyword)
    if not device_id:
        msg = f"No available device for client {client_id} ({client['biz_name']}) today"
        log_session(client, keyword, "N/A", prompt,
                    follow_up, "none", "none:0", "skipped_no_device")
        return jsonify({"error": msg, "status": "no_devices"}), 503

    platform = random.choice(PLATFORMS)
    proxy    = random.choice(PROXY_POOL)

    mark_device_used(device_id, client_id, keyword, platform, "started")

    t = threading.Thread(
        target=run_u2_session,
        args=(client, prompt, follow_up, platform,
              keyword, device_id, dev_info, proxy),
        daemon=True
    )
    t.start()

    return jsonify({
        "status":         "started",
        "client_id":      client_id,
        "client":         client["biz_name"],
        "keyword":        keyword,
        "platform":       platform,
        "device":         device_id,
        "prompt_preview": prompt[:100]
    })


@app.route("/run-all", methods=["POST"])
def run_all():
    """
    Receives a batch of pre-generated sessions from OpenClaw.
    Runs ALL sessions in TRUE PARALLEL — each device gets its own
    thread with its own u2 connection. No port conflicts, no JVM fights.

    Expects JSON:
    {
      "sessions": [
        {
          "client_id": 0,
          "keyword": "bilingual childcare San Francisco",
          "prompt": "the human-like prompt...",
          "follow_up": "optional follow-up..." or null
        },
        ...
      ]
    }
    """
    data = request.get_json()
    if not data or "sessions" not in data:
        return jsonify({"error": "Missing sessions array"}), 400

    sessions    = data["sessions"]
    assignments = load_assignments()
    queued      = []
    skipped     = []

    devices_claimed = set()

    # Check which devices already ran today
    rotation = init_today_rotation()
    today    = str(date.today())
    for device_id in DEVICE_POOL:
        if len(rotation[today].get(device_id, {})) > 0:
            devices_claimed.add(device_id)

    for sess in sessions:
        client_id = int(sess.get("client_id", -1))
        keyword   = sess.get("keyword", "")
        prompt    = sess.get("prompt", "")
        follow_up = sess.get("follow_up", None)

        client = get_client(client_id)
        if not client:
            skipped.append({"client_id": client_id, "keyword": keyword, "reason": "client_not_found"})
            continue

        if not prompt:
            skipped.append({"client": client["biz_name"], "keyword": keyword, "reason": "empty_prompt"})
            continue

        if is_keyword_done_today(client_id, keyword):
            skipped.append({"client": client["biz_name"], "keyword": keyword, "reason": "already_done_today"})
            continue

        # Find a free device
        best_device   = None
        best_dev_info = None

        for device_id, dev_info in DEVICE_POOL.items():
            if device_id in devices_claimed:
                continue
            assigned_clients = assignments.get(device_id, [])
            if client_id not in assigned_clients:
                continue
            best_device   = device_id
            best_dev_info = dev_info
            break

        if not best_device:
            skipped.append({"client": client["biz_name"], "keyword": keyword, "reason": "no_free_device"})
            continue

        platform = random.choice(PLATFORMS)
        proxy    = random.choice(PROXY_POOL)

        mark_device_used(best_device, client_id, keyword, platform, "started")
        devices_claimed.add(best_device)

        print(f"\n   QUEUED: {client['biz_name']} | {keyword} | {platform} | {best_device}")

        queued.append({
            "client":      client,
            "client_name": client["biz_name"],
            "client_id":   client_id,
            "keyword":     keyword,
            "platform":    platform,
            "device":      best_device,
            "dev_info":    best_dev_info,
            "prompt":      prompt,
            "follow_up":   follow_up or "",
            "proxy":       proxy,
            "has_follow_up": follow_up is not None and follow_up != "",
        })

    # ── Launch ALL sessions in PARALLEL ───────────────────────────────
    # Each thread gets its own u2.connect() — zero conflicts
    threads = []
    for sess in queued:
        t = threading.Thread(
            target=run_u2_session,
            args=(
                sess["client"], sess["prompt"], sess["follow_up"],
                sess["platform"], sess["keyword"],
                sess["device"], sess["dev_info"], sess["proxy"]
            ),
            daemon=True
        )
        t.start()
        threads.append(t)
        print(f"   Thread started for {sess['device']}")

    # Build clean response
    queued_response = [{
        "client":       q["client_name"],
        "client_id":    q["client_id"],
        "keyword":      q["keyword"],
        "platform":     q["platform"],
        "device":       q["device"],
        "has_follow_up": q["has_follow_up"],
    } for q in queued]

    return jsonify({
        "status":         "all_running_parallel",
        "total_queued":   len(queued),
        "total_skipped":  len(skipped),
        "queued":         queued_response,
        "skipped":        skipped,
        "devices_used":   [d for d in devices_claimed],
        "devices_free":   [d for d in DEVICE_POOL if d not in devices_claimed],
    })


@app.route("/clients", methods=["GET"])
def get_clients_route():
    clients     = load_clients()
    assignments = load_assignments()
    rotation    = get_rotation_status()

    result = []
    for c in clients:
        assigned_devices = [d for d, ids in assignments.items() if c["id"] in ids]

        keywords_done = []
        for d in DEVICE_POOL:
            device_today = rotation.get(d, {})
            for rot_key, info in device_today.items():
                if rot_key.startswith(f"{c['id']}:"):
                    keywords_done.append(info.get("keyword", rot_key.split(":", 1)[1]))

        keywords_remaining = [k for k in c["keywords"] if k not in keywords_done]

        result.append({
            "id":                c["id"],
            "biz_name":          c["biz_name"],
            "plan":              c["plan"],
            "city":              c["city"],
            "keywords_total":    len(c["keywords"]),
            "keywords_done":     keywords_done,
            "keywords_remaining": keywords_remaining,
            "assigned_devices":  assigned_devices,
        })

    devices_free = [d for d in DEVICE_POOL if not is_device_used_today(d)]

    return jsonify({
        "total":        len(result),
        "devices_free": len(devices_free),
        "devices_free_list": devices_free,
        "clients":      result,
    })


@app.route("/rotation", methods=["GET"])
def rotation_status():
    today    = str(date.today())
    rotation = get_rotation_status()

    summary = {}
    for device_id, sessions in rotation.items():
        summary[device_id] = {
            "sessions_today": len(sessions),
            "sessions": {}
        }
        for rot_key, info in sessions.items():
            client_id_str = rot_key.split(":")[0] if ":" in rot_key else rot_key
            client = get_client(client_id_str)
            summary[device_id]["sessions"][rot_key] = {
                "client_name": client["biz_name"] if client else "unknown",
                "keyword":     info.get("keyword"),
                "platform":    info.get("platform"),
                "status":      info.get("status"),
                "timestamp":   info.get("timestamp"),
                "completed_at": info.get("completed_at", "pending")
            }

    total_sessions = sum(len(s["sessions"]) for s in summary.values())

    return jsonify({
        "date":            today,
        "total_sessions":  total_sessions,
        "devices":         summary
    })


@app.route("/rotation/reset", methods=["POST"])
def rotation_reset():
    today = str(date.today())
    data  = {today: {d: {} for d in DEVICE_POOL}}
    save_rotation(data)
    return jsonify({"status": "reset", "date": today})


@app.route("/devices/check", methods=["GET"])
def check_devices():
    """Health check: verify all devices are connected and u2 agent is running."""
    results = {}
    for device_id, dev_info in DEVICE_POOL.items():
        serial = dev_info["serial"]
        adb_ok = check_device(serial)
        u2_ok  = False

        if adb_ok:
            try:
                d = u2.connect(serial)
                info = d.info
                u2_ok = True
                results[device_id] = {
                    "serial":  serial,
                    "adb":     "ok",
                    "u2":      "ok",
                    "product": info.get("productName", "unknown"),
                    "screen":  f"{info.get('screenSize', {}).get('x', '?')}x{info.get('screenSize', {}).get('y', '?')}",
                    "battery": info.get("batteryLevel", "?"),
                }
            except Exception as e:
                results[device_id] = {
                    "serial": serial,
                    "adb":    "ok",
                    "u2":     f"error: {e}",
                }
        else:
            results[device_id] = {
                "serial": serial,
                "adb":    "not connected",
                "u2":     "n/a",
            }

    all_ok = all(r.get("u2") == "ok" for r in results.values())

    return jsonify({
        "status":  "all_ready" if all_ok else "issues_found",
        "devices": results,
    })


@app.route("/logs", methods=["GET"])
def get_logs():
    try:
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)
        filter_date = request.args.get("date")
        if filter_date:
            logs = [l for l in logs if l.get("date") == filter_date]
        return jsonify({"total": len(logs), "logs": logs})
    except:
        return jsonify({"total": 0, "logs": []})


@app.route("/logs/today", methods=["GET"])
def get_today_logs():
    try:
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)
        today_logs = [l for l in logs if l.get("date") == str(date.today())]
        return jsonify({"total": len(today_logs), "logs": today_logs})
    except:
        return jsonify({"total": 0, "logs": []})


@app.route("/health", methods=["GET"])
def health():
    clients     = load_clients()
    rotation    = get_rotation_status()

    total_sessions_today = sum(len(s) for s in rotation.values())

    return jsonify({
        "status":               "ok",
        "runner":               "u2 v5.0",
        "date":                 str(date.today()),
        "clients_total":        len(clients),
        "devices_total":        len(DEVICE_POOL),
        "sessions_today":       total_sessions_today,
        "platforms":            PLATFORMS,
        "parallel":             True,
    })


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n   AEO u2 Runner v5.0")
    print("="*60)
    print(f"   Clients     : {CLIENTS_FILE}")
    print(f"   Devices     : {len(DEVICE_POOL)} in pool")
    print(f"   Assignments : {ASSIGNMENTS_FILE}")
    print(f"   Platforms   : {', '.join(PLATFORMS)}")
    print(f"   Parallel    : YES (true multi-device)")
    print()

    # Pre-check devices
    print("   Checking devices...")
    all_ok = True
    for device_id, dev_info in DEVICE_POOL.items():
        serial = dev_info["serial"]
        if check_device(serial):
            try:
                d = u2.connect(serial)
                info = d.info
                print(f"      {device_id} ({serial}): {info.get('productName', 'ok')}")
            except Exception as e:
                print(f"      {device_id} ({serial}): ADB ok, u2 agent NOT running")
                print(f"         Fix: python -m uiautomator2 init --serial {serial}")
                all_ok = False
        else:
            print(f"      {device_id} ({serial}): NOT CONNECTED")
            all_ok = False

    if not all_ok:
        print("\n      Some devices have issues. Fix them before running sessions.")
        print("      The server will still start — fix devices and they'll work.\n")

    print()
    print(f"   run-all     : POST http://localhost:5001/run-all")
    print(f"   run-aeo     : POST http://localhost:5001/run-aeo")
    print(f"   clients     : GET  http://localhost:5001/clients")
    print(f"   rotation    : GET  http://localhost:5001/rotation")
    print(f"   logs today  : GET  http://localhost:5001/logs/today")
    print(f"   reset       : POST http://localhost:5001/rotation/reset")
    print(f"   devices     : GET  http://localhost:5001/devices/check")
    print(f"   health      : GET  http://localhost:5001/health")
    print("="*60)
    app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)