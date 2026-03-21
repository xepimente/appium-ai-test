"""
AEO Appium OpenClaw Flask API — v2.0
--------------------------------------
REST API for OpenClaw to trigger AEO sessions.
OpenClaw sends prompt + follow_up, this server executes on devices.

Rotation rule: 1 device + 1 client = 1 keyword per day.
A device CAN serve multiple clients (1 keyword each).
Each keyword runs on exactly 1 device per day.

Endpoints:
  POST /run-aeo    — run one client/keyword session
  POST /run-all    — run a batch of sessions (OpenClaw sends all at once)
  GET  /status     — today's rotation
  GET  /health     — connected devices + Appium server status
  POST /reset      — reset today's rotation
  GET  /logs       — session logs (optional ?date=YYYY-MM-DD)
  GET  /logs/today — today's logs
  GET  /clients    — client list with keyword status
"""

import json
import os
import random
import subprocess
import sys
import threading
from datetime import date, datetime, timezone

from flask import Flask, request, jsonify

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from session_runner import run_parallel
from main import (
    _load_device_pool,
    get_adb_transport_map,
    resolve_serial,
    load_clients,
    load_rotation,
    save_rotation,
    is_keyword_done_today,
    has_device_done_client_today,
    mark_device_used,
    update_status,
    log_session,
    get_today_rotation,
    PLATFORMS,
    LOG_FILE,
    BASE_APPIUM_PORT,
)


app = Flask(__name__)

DEVICE_POOL = _load_device_pool()


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_client(client_id):
    clients = load_clients()
    try:
        return next((c for c in clients if c["id"] == int(client_id)), None)
    except Exception:
        return None


def check_appium_server(port):
    """Return True if the Appium server at this port is responding."""
    try:
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             f"http://localhost:{port}/wd/hub/status"],
            capture_output=True, text=True, timeout=3,
        )
        return result.stdout.strip() == "200"
    except Exception:
        return False


def find_device_for_client(client_id, devices_claimed=None):
    """
    Find a device that hasn't served this client today.
    Respects the 1-device-per-client-per-day rule.
    Returns (device_id, dev_info) or (None, None).
    """
    if devices_claimed is None:
        devices_claimed = set()

    for device_id, dev_info in DEVICE_POOL.items():
        if device_id in devices_claimed:
            continue
        if has_device_done_client_today(device_id, client_id):
            continue
        return device_id, dev_info
    return None, None


# ── POST /run-aeo ─────────────────────────────────────────────────────────────

@app.route("/run-aeo", methods=["POST"])
def run_aeo():
    """
    Run a single AEO session. Called by OpenClaw with prompt ready.

    Body:
      {
        "client_id": 0,
        "keyword":   "bilingual childcare San Francisco",
        "prompt":    "I heard Mae's Childcare...",
        "follow_up": "Cool, do they post updates...",   # optional
        "platform":  "Gemini"                           # optional — random if missing
      }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    client_id = int(data.get("client_id", 0))
    client    = get_client(client_id)
    if not client:
        return jsonify({"error": f"Client {client_id} not found"}), 400

    keyword   = data.get("keyword")
    prompt    = data.get("prompt")
    follow_up = data.get("follow_up", None)
    platform  = data.get("platform") or random.choice(PLATFORMS)

    if not keyword:
        return jsonify({"error": "Missing keyword"}), 400

    if not prompt:
        return jsonify({"error": "Missing prompt (OpenClaw should send it)"}), 400

    if platform not in PLATFORMS:
        return jsonify({"error": f"Invalid platform: {platform}. Valid: {PLATFORMS}"}), 400

    # Check rotation: keyword already done today?
    if is_keyword_done_today(client_id, keyword):
        return jsonify({
            "error":  f"Keyword '{keyword}' already done today for client {client_id}",
            "status": "skipped_already_done",
        }), 409

    # Find a device that hasn't served this client today
    device_id, dev_info = find_device_for_client(client_id)
    if not device_id:
        log_session(client, keyword, "N/A", prompt, follow_up, "none", "skipped_no_device")
        return jsonify({
            "error":  f"All devices already served client {client_id} ({client['biz_name']}) today",
            "status": "no_devices",
        }), 503

    transport_map = get_adb_transport_map()
    short_serial  = dev_info["serial"]
    full_serial   = resolve_serial(short_serial, transport_map)
    port          = dev_info.get("port", BASE_APPIUM_PORT)

    mark_device_used(device_id, client_id, keyword, platform)

    sess = {
        "client":      client,
        "keyword":     keyword,
        "platform":    platform,
        "device_id":   device_id,
        "serial":      short_serial,
        "full_serial": full_serial,
        "port":        port,
        "prompt":      prompt,
        "follow_up":   follow_up,
    }

    def on_complete(s, result):
        status = "success" if result.get("success") else "error"
        update_status(s["device_id"], s["client"]["id"], s["keyword"], status)
        log_session(s["client"], s["keyword"], s["platform"],
                    s["prompt"], s.get("follow_up"), s["device_id"], status)

    t = threading.Thread(
        target=run_parallel,
        args=([sess],),
        kwargs={"on_complete": on_complete},
        daemon=True,
    )
    t.start()

    return jsonify({
        "status":         "started",
        "client_id":      client_id,
        "client":         client["biz_name"],
        "keyword":        keyword,
        "platform":       platform,
        "device":         device_id,
        "port":           port,
        "prompt_preview": prompt[:100],
    })


# ── POST /run-all ─────────────────────────────────────────────────────────────

@app.route("/run-all", methods=["POST"])
def run_all():
    """
    Run a batch of sessions in TRUE PARALLEL. Called by OpenClaw.
    OpenClaw sends prompts pre-generated. Each session gets assigned
    to a device respecting: 1 device + 1 client = 1 keyword per day.

    Body:
      {
        "sessions": [
          {"client_id": 0, "keyword": "...", "prompt": "...", "follow_up": "..."},
          {"client_id": 1, "keyword": "...", "prompt": "...", "follow_up": "..."},
          ...
        ]
      }
    """
    data = request.get_json()
    if not data or "sessions" not in data:
        return jsonify({"error": "Missing sessions array"}), 400

    sessions_input = data["sessions"]
    transport_map  = get_adb_transport_map()
    queued         = []
    skipped        = []

    # Track which (device, client) pairs are claimed in this batch
    device_client_claimed = {d: set() for d in DEVICE_POOL}

    for item in sessions_input:
        client_id = int(item.get("client_id", -1))
        keyword   = item.get("keyword", "")
        prompt    = item.get("prompt", "")
        follow_up = item.get("follow_up", None)
        platform  = item.get("platform") or random.choice(PLATFORMS)

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

        # Find a device that hasn't served this client (today + this batch)
        best_device = None
        best_dev    = None
        for device_id, dev_info in DEVICE_POOL.items():
            if client_id in device_client_claimed[device_id]:
                continue
            if has_device_done_client_today(device_id, client_id):
                continue
            best_device = device_id
            best_dev    = dev_info
            break

        if not best_device:
            skipped.append({"client": client["biz_name"], "keyword": keyword, "reason": "all_devices_served_this_client"})
            continue

        short_serial = best_dev["serial"]
        full_serial  = resolve_serial(short_serial, transport_map)
        port         = best_dev.get("port", BASE_APPIUM_PORT)

        mark_device_used(best_device, client_id, keyword, platform)
        device_client_claimed[best_device].add(client_id)

        queued.append({
            "client":      client,
            "keyword":     keyword,
            "platform":    platform,
            "device_id":   best_device,
            "serial":      short_serial,
            "full_serial": full_serial,
            "port":        port,
            "prompt":      prompt,
            "follow_up":   follow_up,
        })

    if queued:
        def on_complete(s, result):
            status = "success" if result.get("success") else "error"
            update_status(s["device_id"], s["client"]["id"], s["keyword"], status)
            log_session(s["client"], s["keyword"], s["platform"],
                        s["prompt"], s.get("follow_up"), s["device_id"], status)
            print(f"Logged: {s['client']['biz_name']} | {s['keyword'][:30]} | {status}")

        t = threading.Thread(
            target=run_parallel,
            args=(queued,),
            kwargs={"on_complete": on_complete},
            daemon=True,
        )
        t.start()

    queued_response = [{
        "client":        q["client"]["biz_name"],
        "client_id":     q["client"]["id"],
        "keyword":       q["keyword"],
        "platform":      q["platform"],
        "device":        q["device_id"],
        "port":          q["port"],
        "has_follow_up": bool(q.get("follow_up")),
    } for q in queued]

    return jsonify({
        "status":        "all_running_parallel",
        "total_queued":  len(queued),
        "total_skipped": len(skipped),
        "queued":        queued_response,
        "skipped":       skipped,
    })


# ── GET /status ───────────────────────────────────────────────────────────────

@app.route("/status", methods=["GET"])
def status():
    today    = str(date.today())
    rotation = get_today_rotation()

    summary = {}
    for device_id, dev_info in DEVICE_POOL.items():
        sessions = rotation.get(device_id, {})
        clients_served = set()
        session_list   = {}
        for rot_key, info in sessions.items():
            cid = rot_key.split(":")[0]
            clients_served.add(cid)
            c = get_client(cid)
            session_list[rot_key] = {
                "client_name":  c["biz_name"] if c else "unknown",
                "keyword":      info.get("keyword"),
                "platform":     info.get("platform"),
                "status":       info.get("status"),
                "completed_at": info.get("completed_at", "pending"),
            }
        summary[device_id] = {
            "serial":          dev_info["serial"],
            "port":            dev_info.get("port"),
            "clients_served":  len(clients_served),
            "sessions_today":  len(sessions),
            "sessions":        session_list,
        }

    total = sum(len(s["sessions"]) for s in summary.values())
    return jsonify({"date": today, "total_sessions": total, "devices": summary})


# ── GET /clients ──────────────────────────────────────────────────────────────

@app.route("/clients", methods=["GET"])
def get_clients_route():
    """
    Client list with full data + today's keyword status.
    This is the SINGLE SOURCE OF TRUTH for OpenClaw.
    OpenClaw reads clients from here, NOT from its own files.
    """
    clients   = load_clients()
    rotation  = get_today_rotation()

    # Calculate device capacity: how many more clients each device can serve today
    devices_total   = len(DEVICE_POOL)
    all_clients_ids = [c["id"] for c in clients]

    # Count how many (device, client) slots are still available
    total_remaining_slots = 0
    for device_id in DEVICE_POOL:
        for cid in all_clients_ids:
            if not has_device_done_client_today(device_id, cid):
                total_remaining_slots += 1

    result = []
    for c in clients:
        done = []
        for d in DEVICE_POOL:
            for rot_key, info in rotation.get(d, {}).items():
                if rot_key.startswith(f"{c['id']}:"):
                    done.append({
                        "keyword":  info.get("keyword"),
                        "device":   d,
                        "platform": info.get("platform"),
                        "status":   info.get("status"),
                    })

        done_keywords = [d["keyword"] for d in done]
        remaining     = [k for k in c["keywords"] if k not in done_keywords]

        # How many devices can still serve THIS client today
        devices_available = sum(
            1 for d in DEVICE_POOL
            if not has_device_done_client_today(d, c["id"])
        )

        result.append({
            "id":                  c["id"],
            "biz_name":            c["biz_name"],
            "city":                c["city"],
            "state":               c.get("state", ""),
            "gmb_url":             c.get("gmb_url", ""),
            "keywords_total":      len(c["keywords"]),
            "keywords_done":       done,
            "keywords_remaining":  remaining,
            "devices_available":   devices_available,
        })

    # Total sessions possible today = min(remaining keywords, available slots)
    total_remaining_keywords = sum(len(c["keywords_remaining"]) for c in result)

    return jsonify({
        "total_clients":            len(result),
        "devices_total":            devices_total,
        "total_remaining_keywords": total_remaining_keywords,
        "total_remaining_slots":    total_remaining_slots,
        "clients":                  result,
    })


# ── GET /health ───────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    transport_map = get_adb_transport_map()
    clients       = load_clients()
    rotation      = get_today_rotation()
    today_count   = sum(len(s) for s in rotation.values())

    device_status = {}
    for device_id, dev_info in DEVICE_POOL.items():
        short     = dev_info["serial"]
        port      = dev_info.get("port")
        connected = short in transport_map
        appium_up = check_appium_server(port) if port else False

        device_status[device_id] = {
            "serial":    short,
            "connected": connected,
            "port":      port,
            "appium":    "up" if appium_up else "down",
        }

    all_up = all(d["appium"] == "up" for d in device_status.values())

    return jsonify({
        "status":         "ok" if all_up else "degraded",
        "runner":         "appium v2.0",
        "date":           str(date.today()),
        "clients_total":  len(clients),
        "devices_total":  len(DEVICE_POOL),
        "sessions_today": today_count,
        "platforms":      PLATFORMS,
        "parallel":       True,
        "devices":        device_status,
    })


# ── POST /reset ───────────────────────────────────────────────────────────────

@app.route("/reset", methods=["POST"])
def reset():
    today = str(date.today())
    save_rotation({today: {d: {} for d in DEVICE_POOL}})
    return jsonify({"status": "reset", "date": today})


# ── GET /logs ─────────────────────────────────────────────────────────────────

@app.route("/logs", methods=["GET"])
def get_logs():
    try:
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)
        filter_date = request.args.get("date")
        if filter_date:
            logs = [l for l in logs if l.get("date") == filter_date]
        return jsonify({"total": len(logs), "logs": logs})
    except Exception:
        return jsonify({"total": 0, "logs": []})


@app.route("/logs/today", methods=["GET"])
def get_today_logs():
    try:
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)
        today_logs = [l for l in logs if l.get("date") == str(date.today())]
        return jsonify({"total": len(today_logs), "logs": today_logs})
    except Exception:
        return jsonify({"total": 0, "logs": []})


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\nAEO Appium OpenClaw API v2.0")
    print("=" * 60)
    print(f"Devices in pool : {len(DEVICE_POOL)}")
    print(f"Platforms       : {', '.join(PLATFORMS)}")
    print(f"Rotation rule   : 1 device + 1 client = 1 keyword/day")
    for device_id, info in DEVICE_POOL.items():
        print(f"  {device_id}  serial:{info['serial'][:24]}  port:{info.get('port')}")
    print()
    print(f"POST /run-aeo    — single session (OpenClaw sends prompt)")
    print(f"POST /run-all    — batch sessions")
    print(f"GET  /status     — today's rotation")
    print(f"GET  /clients    — client keyword status")
    print(f"GET  /health     — device + Appium status")
    print(f"POST /reset      — reset rotation")
    print(f"GET  /logs/today — today's logs")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)
