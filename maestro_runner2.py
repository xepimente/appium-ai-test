"""
AEO Maestro Runner — v4.1.1
--------------------------
- Multi-client support via clients.json
- Static device assignments via device_assignments.json
- Rotation by KEYWORD: same keyword never runs twice per day (any device)
- Same device CAN run multiple keywords for same client
- /run-all endpoint: loops all clients x all keywords continuously
- Platform randomization per session
- Platform-specific YAML flows
- Per-device threading lock (prevents ADB collisions)
- Proxy integration via ADB
- Full combined session logging
- v4.1: ADB pre-warm, forward cleanup, extended driver timeout, stdout+stderr logging
- v4.1.1: ASCII sanitizer for prompts (Maestro Unicode fix)
"""


import json
import os
import random
import threading
import subprocess
import time
from datetime import datetime, date, timezone
from flask import Flask, request, jsonify


app = Flask(__name__)


LOG_FILE         = "sessions_log.json"
CLIENTS_FILE     = "clients.json"
ASSIGNMENTS_FILE = "device_assignments.json"
ROTATION_FILE    = "device_rotation.json"

# Stagger delay (seconds) between launching devices to avoid ADB port forwarding races
DEVICE_LAUNCH_STAGGER = 15


# ── Platform-specific Maestro Flows ───────────────────────────────────────────
# Each platform gets its own YAML because the UI interactions differ
PLATFORM_YAML = {
    "Gemini":     "flows/gemini_session.yaml",
    "ChatGPT":    "flows/chatgpt_session.yaml",
    "Perplexity": "flows/perplexity_session.yaml",
}


# ── Platforms ─────────────────────────────────────────────────────────────────

PLATFORM_URLS = {
    "Gemini":     "https://gemini.google.com",
    "ChatGPT":    "https://chat.openai.com",
    "Perplexity": "https://www.perplexity.ai"
}
PLATFORM_NAMES = {
    "Gemini":     "Google Gemini",
    "ChatGPT":    "ChatGPT",
    "Perplexity": "Perplexity AI"
}
PLATFORM_DOMAINS = {
    "Gemini":     "gemini.google.com",
    "ChatGPT":    "chat.openai.com",
    "Perplexity": "www.perplexity.ai"
}
PLATFORM_TITLES = {
    "Gemini":     "Google Gemini",
    "ChatGPT":    "ChatGPT",
    "Perplexity": "Perplexity"
}


# ── Device Pool ───────────────────────────────────────────────────────────────
DEVICE_POOL = {
    "device-001": {"serial": "0B64C27G23101E10", "maestro_port": "7001"},
    "device-002": {"serial": "324651961440", "maestro_port": "7002"},
}

# Per-device locks
DEVICE_LOCKS = {device_id: threading.Lock() for device_id in DEVICE_POOL}


# ── Proxy Pool ────────────────────────────────────────────────────────────────
PROXY_POOL = [
    "none:0",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def utcnow_iso():
    """Timezone-aware UTC timestamp (no deprecation warning)."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def prewarm_device(serial):
    """Ensure ADB connection is alive before Maestro grabs it."""
    try:
        r = subprocess.run(
            ["adb", "-s", serial, "shell", "echo", "ready"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            print(f"   🔌 Device {serial} pre-warm OK")
        else:
            print(f"   ⚠️  Device {serial} pre-warm failed: {r.stderr.strip()}")
    except Exception as e:
        print(f"   ⚠️  Device {serial} pre-warm error: {e}")


def clean_forwards(serial):
    """Remove stale ADB port forwards that can block new Maestro sessions."""
    try:
        subprocess.run(
            ["adb", "-s", serial, "forward", "--remove-all"],
            capture_output=True, text=True, timeout=10
        )
        print(f"   🧹 Cleared ADB forwards for {serial}")
    except Exception as e:
        print(f"   ⚠️  Could not clear forwards for {serial}: {e}")


def sanitize_for_maestro(text):
    """
    Strip non-ASCII characters that Maestro's inputText can't handle.
    See: https://github.com/mobile-dev-inc/maestro/issues/146
    """
    if not text:
        return text
    replacements = {
        "\u2014": "-",    # em dash —
        "\u2013": "-",    # en dash –
        "\u2018": "'",    # left single quote '
        "\u2019": "'",    # right single quote '
        "\u201c": '"',    # left double quote "
        "\u201d": '"',    # right double quote "
        "\u2026": "...",  # ellipsis …
        "\u00e9": "e",    # é
        "\u00e8": "e",    # è
        "\u00f1": "n",    # ñ
        "\u00a0": " ",    # non-breaking space
        "\u200b": "",     # zero-width space
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    # Fallback: replace any remaining non-ASCII
    return text.encode("ascii", "replace").decode("ascii").replace("?", "")


# ── Clients ───────────────────────────────────────────────────────────────────

def load_clients():
    try:
        with open(CLIENTS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Could not load {CLIENTS_FILE}: {e}")
        return []


def get_client(client_id):
    clients = load_clients()
    try:
        return next((c for c in clients if c["id"] == int(client_id)), None)
    except:
        return None


# ── Device Assignments ────────────────────────────────────────────────────────

def load_assignments():
    """
    Returns dict: { "device-001": [0, 1], "device-002": [0, 1] }
    """
    try:
        with open(ASSIGNMENTS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Could not load {ASSIGNMENTS_FILE}: {e}")
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
        print(f"   ⚠️  Keyword '{keyword}' for client {client_id} already done today — skipping globally")
        return None, None

    for device_id, dev_info in DEVICE_POOL.items():
        assigned_clients = assignments.get(device_id, [])
        if client_id not in assigned_clients:
            continue
        if is_device_used_today(device_id):
            print(f"   ⚠️  {device_id} already used today — skipping")
            continue
        return device_id, dev_info

    return None, None


def mark_device_used(device_id, client_id, keyword, platform, status="started"):
    today    = str(date.today())
    rotation = load_rotation()
    rot_key  = f"{client_id}:{keyword}"

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
    rotation = load_rotation()
    rot_key  = f"{client_id}:{keyword}"
    try:
        rotation[today][device_id][rot_key]["status"] = status
        rotation[today][device_id][rot_key]["completed_at"] = utcnow_iso()
        save_rotation(rotation)
    except:
        pass


def get_rotation_status():
    today    = str(date.today())
    rotation = load_rotation()
    return rotation.get(today, {d: {} for d in DEVICE_POOL})


# ── Proxy ─────────────────────────────────────────────────────────────────────

def set_proxy(adb_serial, proxy_str):
    parts  = proxy_str.split(":")
    host   = parts[0]
    port   = parts[1]
    cmd    = ["adb", "-s", adb_serial, "shell",
              "settings", "put", "global", "http_proxy", f"{host}:{port}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"   🌐 Proxy set → {host}:{port}")
    else:
        print(f"   ⚠️  Proxy set failed: {result.stderr.strip()}")


def clear_proxy(adb_serial):
    cmd = ["adb", "-s", adb_serial, "shell",
           "settings", "put", "global", "http_proxy", ":0"]
    subprocess.run(cmd, capture_output=True, text=True)
    print(f"   🧹 Proxy cleared")


# ── Logging ───────────────────────────────────────────────────────────────────

def log_session(client, keyword, platform, prompt, follow_up,
                device_id, proxy, status):
    entry = {
        "timestamp":    utcnow_iso(),
        "date":         str(date.today()),

        # Client details
        "client_id":    client["id"],
        "client_name":  client["biz_name"],
        "client_plan":  client["plan"],
        "client_city":  client["city"],
        "client_state": client["state"],

        # Session details
        "keyword":      keyword,
        "platform":     platform,
        "prompt":       prompt,
        "follow_up":    follow_up,
        "has_follow_up": follow_up is not None and follow_up != "",

        # Device details
        "device_id":    device_id,
        "proxy": proxy if proxy == "none:0" else proxy.split(":")[0] + ":" + proxy.split(":")[1],

        # Result
        "status":       status,
        "runner":       "maestro"
    }

    try:
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)
    except:
        logs = []

    logs.append(entry)

    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)

    print(f"📋 Logged → {LOG_FILE} [{status}]")


# ── Maestro Session ───────────────────────────────────────────────────────────

def run_maestro_session(client, prompt, follow_up, platform,
                        keyword, device_id, dev_info, proxy):

    # ── v4.1: Sanitize prompts to ASCII (Maestro can't type Unicode) ──
    prompt    = sanitize_for_maestro(prompt)
    follow_up = sanitize_for_maestro(follow_up) if follow_up else ""

    yaml_file  = PLATFORM_YAML.get(platform)
    if not yaml_file:
        print(f"❌ No YAML flow defined for platform: {platform}")
        update_device_status(device_id, client["id"], keyword, "error_no_yaml")
        log_session(client, keyword, platform, prompt,
                    follow_up, device_id, proxy, "error_no_yaml")
        return

    url        = PLATFORM_URLS.get(platform,    "https://gemini.google.com")
    name       = PLATFORM_NAMES.get(platform,   "Google Gemini")
    domain     = PLATFORM_DOMAINS.get(platform, "gemini.google.com")
    title      = PLATFORM_TITLES.get(platform,  "Google Gemini")
    has_follow = "true" if follow_up else "false"
    status     = "success"

    adb_serial   = dev_info["serial"]
    maestro_port = dev_info["maestro_port"]

    print(f"\n{'='*60}")
    print(f"📱 Client   : {client['biz_name']} (ID: {client['id']})")
    print(f"🎯 Platform : {platform}")
    print(f"📄 Flow     : {yaml_file}")
    print(f"🔑 Keyword  : {keyword}")
    print(f"📱 Device   : {device_id} ({adb_serial}) port {maestro_port}")
    print(f"🌐 Proxy    : {proxy.split(':')[0]}:{proxy.split(':')[1]}")
    print(f"📝 Prompt   : {prompt[:80]}...")
    if follow_up:
        print(f"💬 Follow-up: {follow_up[:60]}...")
    print(f"{'='*60}")

    # Acquire device lock
    lock = DEVICE_LOCKS[device_id]
    print(f"🔒 Waiting for {device_id} lock...")
    lock.acquire()
    print(f"🔓 Acquired {device_id} lock")

    try:
        # ── v4.1: Pre-flight device prep ──────────────────────────────
        clean_forwards(adb_serial)
        prewarm_device(adb_serial)

        # Set ANDROID_SERIAL + extended driver timeout
        env = os.environ.copy()
        env["ANDROID_SERIAL"] = adb_serial
        env["MAESTRO_PORT"]   = maestro_port
        env["MAESTRO_DRIVER_STARTUP_TIMEOUT"] = "60000"  # 60s (default is 15s)

        debug_dir = f"/tmp/maestro-{device_id}"
        print(f"\n▶️  Running maestro test → {yaml_file} on {adb_serial} (MAESTRO_PORT={maestro_port})")
        result = subprocess.run(
            [
                "maestro", "test",
                "--device", adb_serial,
                "--debug-output", debug_dir,
                yaml_file,
                "--env", f"PLATFORM_URL={url}",
                "--env", f"PLATFORM_NAME={name}",
                "--env", f"PLATFORM_DOMAIN={domain}",
                "--env", f"PLATFORM_TITLE={title}",
                "--env", f"PROMPT={prompt}",
                "--env", f"FOLLOW_UP={follow_up or ''}",
                "--env", f"HAS_FOLLOW_UP={has_follow}",
                "--env", f"DEVICE_ID={device_id}",
                "--env", f"CLIENT_ID={client['id']}",
                "--env", f"CLIENT_NAME={client['biz_name']}"
            ],
            capture_output=True,
            text=True,
            timeout=240,
            env=env,
        )

        if result.returncode != 0:
            # ── v4.1: Print BOTH stderr and stdout for debugging ──────
            print(f"❌ Maestro STDERR:\n{result.stderr}")
            print(f"❌ Maestro STDOUT:\n{result.stdout[-1000:] if result.stdout else '(empty)'}")
            status = "error"
        else:
            print(f"✅ Session complete")
            print(result.stdout[-500:] if result.stdout else "")

    except subprocess.TimeoutExpired:
        print(f"⏱️  Timed out after 240s")
        status = "timeout"
    except FileNotFoundError:
        print(f"❌ maestro not found")
        status = "error_maestro_not_found"
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        status = "error"
    finally:
        lock.release()
        print(f"🔓 Released {device_id} lock")

    # Update rotation with final status
    update_device_status(device_id, client["id"], keyword, status)

    # Log full session details
    log_session(client, keyword, platform, prompt,
                follow_up, device_id, proxy, status)


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
        print(f"⚠️  {msg}")
        log_session(client, keyword, "N/A", prompt,
                    follow_up, "none", "none:0", "skipped_no_device")
        return jsonify({"error": msg, "status": "no_devices"}), 503

    platform = random.choice(["Gemini", "ChatGPT", "Perplexity"])
    proxy    = random.choice(PROXY_POOL)

    mark_device_used(device_id, client_id, keyword, platform, "started")

    print(f"\n🦞 {client['biz_name']} | {keyword} | {platform} | {device_id} | {PLATFORM_YAML[platform]}")

    t = threading.Thread(
        target=run_maestro_session,
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
        "yaml_flow":      PLATFORM_YAML[platform],
        "device":         device_id,
        "prompt_preview": prompt[:100]
    })


@app.route("/clients", methods=["GET"])
def get_clients():
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


@app.route("/run-all", methods=["POST"])
def run_all():
    """
    Receives a batch of pre-generated sessions from OpenClaw.
    Distributes across available devices, queues them all.
    Sessions run sequentially per device (device lock).

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

    # Track which devices are already claimed in this batch
    devices_claimed = set()

    # Also check which devices already ran today (from previous runs)
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
            skipped.append({
                "client_id": client_id,
                "keyword":   keyword,
                "reason":    "client_not_found"
            })
            continue

        if not prompt:
            skipped.append({
                "client":  client["biz_name"],
                "keyword": keyword,
                "reason":  "empty_prompt"
            })
            continue

        if is_keyword_done_today(client_id, keyword):
            skipped.append({
                "client":  client["biz_name"],
                "keyword": keyword,
                "reason":  "already_done_today"
            })
            continue

        # Find a FREE device assigned to this client
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
            skipped.append({
                "client":  client["biz_name"],
                "keyword": keyword,
                "reason":  "no_free_device"
            })
            continue

        # Pick platform + proxy
        platform = random.choice(["Gemini", "ChatGPT", "Perplexity"])
        proxy    = random.choice(PROXY_POOL)

        # Mark used immediately
        mark_device_used(best_device, client_id, keyword, platform, "started")
        devices_claimed.add(best_device)

        print(f"\n🦞 QUEUED: {client['biz_name']} | {keyword} | {platform} | {best_device}")

        # Stagger device launches to avoid ADB port forwarding races
        if len(queued) > 0:
            print(f"   ⏳ Waiting {DEVICE_LAUNCH_STAGGER}s before launching next device...")
            time.sleep(DEVICE_LAUNCH_STAGGER)

        # Launch in thread
        t = threading.Thread(
            target=run_maestro_session,
            args=(client, prompt, follow_up or "", platform,
                  keyword, best_device, best_dev_info, proxy),
            daemon=True
        )
        t.start()

        queued.append({
            "client":    client["biz_name"],
            "client_id": client_id,
            "keyword":   keyword,
            "platform":  platform,
            "device":    best_device,
            "has_follow_up": follow_up is not None and follow_up != "",
        })

    return jsonify({
        "status":         "all_queued",
        "total_queued":   len(queued),
        "total_skipped":  len(skipped),
        "queued":         queued,
        "skipped":        skipped,
        "devices_used":    [d for d in devices_claimed],
        "devices_free":    [d for d in DEVICE_POOL if d not in devices_claimed],
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
    assignments = load_assignments()

    total_sessions_today = sum(len(s) for s in rotation.values())

    return jsonify({
        "status":               "ok",
        "runner":               "maestro v4.1.1",
        "date":                 str(date.today()),
        "clients_total":        len(clients),
        "devices_total":        len(DEVICE_POOL),
        "sessions_today":       total_sessions_today,
        "platforms":            list(PLATFORM_URLS.keys()),
        "yaml_flows":           PLATFORM_YAML,
    })


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🦞 AEO Maestro Runner v4.1.1")
    print("="*60)
    print(f"📋 Clients     : {CLIENTS_FILE}")
    print(f"📱 Devices     : {len(DEVICE_POOL)} in pool")
    print(f"🔧 Assignments : {ASSIGNMENTS_FILE}")
    print(f"🌐 Proxies     : {len(PROXY_POOL)} in pool")
    print(f"📄 YAML Flows  :")
    for plat, yml in PLATFORM_YAML.items():
        print(f"   {plat:12s} → {yml}")
    print(f"🚀 run-all     : POST http://localhost:5001/run-all")
    print(f"⚡ run-aeo     : POST http://localhost:5001/run-aeo")
    print(f"👥 clients     : GET  http://localhost:5001/clients")
    print(f"📊 rotation    : GET  http://localhost:5001/rotation")
    print(f"📋 logs today  : GET  http://localhost:5001/logs/today")
    print(f"🔄 reset       : POST http://localhost:5001/rotation/reset")
    print(f"❤️  health     : GET  http://localhost:5001/health")
    print("="*60)
    app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)