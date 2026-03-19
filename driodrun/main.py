"""
AEO DroidRun Orchestrator — v6.0
---------------------------------
Self-contained: generates prompts, distributes to devices, runs in parallel.
No OpenClaw needed — everything runs from this single entry point.

Usage:
  python main.py                  # run daily AEO sessions
  python main.py --audit          # run weekly ranking audit
  python main.py --status         # show today's status
  python main.py --reset          # reset today's rotation (testing)
"""

import json
import os
import sys
import random
import asyncio
import argparse
from datetime import datetime, date

# Add parent dir so agents/ imports work
sys.path.insert(0, os.path.dirname(__file__))

from agents.prompt_generator import generate_session_prompts
from agents.ranking_auditor import generate_ranking_prompt
from agents.session_runner import run_parallel


# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR    = os.path.dirname(BASE_DIR)  # parent aeo/ folder
CLIENTS_FILE   = os.path.join(PROJECT_DIR, "clients.json")
ASSIGNMENTS_FILE = os.path.join(PROJECT_DIR, "device_assignments.json")
ROTATION_FILE  = os.path.join(PROJECT_DIR, "device_rotation.json")
LOG_FILE       = os.path.join(BASE_DIR, "logs", "sessions_log.json")


# ── Device Pool ──────────────────────────────────────────────────────────────
DEVICE_POOL = {
    "device-001": {"serial": "324651961440"},
    "device-002": {"serial": "0B64C27G23101E10"},
}

PLATFORMS = ["Gemini", "ChatGPT", "Perplexity"]


# ── Data Loaders ─────────────────────────────────────────────────────────────

def load_clients():
    with open(CLIENTS_FILE, "r") as f:
        return json.load(f)


def load_assignments():
    try:
        with open(ASSIGNMENTS_FILE, "r") as f:
            return json.load(f)
    except:
        return {d: [c["id"] for c in load_clients()] for d in DEVICE_POOL}


def load_rotation():
    try:
        with open(ROTATION_FILE, "r") as f:
            return json.load(f)
    except:
        return {}


def save_rotation(data):
    with open(ROTATION_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ── Rotation Logic ───────────────────────────────────────────────────────────

def get_today_rotation():
    today = str(date.today())
    rotation = load_rotation()
    if today not in rotation:
        rotation[today] = {d: {} for d in DEVICE_POOL}
        save_rotation(rotation)
    return rotation[today]


def is_keyword_done_today(client_id, keyword):
    """Check if this keyword has run on ANY device today."""
    today_rot = get_today_rotation()
    rot_key = f"{client_id}:{keyword}"
    for device_id in DEVICE_POOL:
        if rot_key in today_rot.get(device_id, {}):
            return True
    return False


def is_device_used_today(device_id):
    """1 device = 1 session per day."""
    today_rot = get_today_rotation()
    return len(today_rot.get(device_id, {})) > 0


def get_free_devices():
    """Return list of device IDs not yet used today."""
    return [d for d in DEVICE_POOL if not is_device_used_today(d)]


def mark_device_used(device_id, client_id, keyword, platform):
    today = str(date.today())
    rotation = load_rotation()
    if today not in rotation:
        rotation[today] = {d: {} for d in DEVICE_POOL}
    if device_id not in rotation[today]:
        rotation[today][device_id] = {}
    rot_key = f"{client_id}:{keyword}"
    rotation[today][device_id][rot_key] = {
        "client_id": client_id,
        "keyword": keyword,
        "platform": platform,
        "status": "started",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    save_rotation(rotation)


def update_status(device_id, client_id, keyword, status):
    today = str(date.today())
    rotation = load_rotation()
    rot_key = f"{client_id}:{keyword}"
    try:
        rotation[today][device_id][rot_key]["status"] = status
        rotation[today][device_id][rot_key]["completed_at"] = datetime.utcnow().isoformat() + "Z"
        save_rotation(rotation)
    except:
        pass


# ── Logging ──────────────────────────────────────────────────────────────────

def log_session(client, keyword, platform, prompt, follow_up, device_id, status):
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "date": str(date.today()),
        "client_id": client["id"],
        "client_name": client["biz_name"],
        "city": client["city"],
        "keyword": keyword,
        "platform": platform,
        "prompt": prompt,
        "follow_up": follow_up,
        "has_follow_up": follow_up is not None,
        "device_id": device_id,
        "status": status,
        "runner": "droidrun",
    }

    try:
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)
    except:
        logs = []

    logs.append(entry)
    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)


# ── Session Planning ─────────────────────────────────────────────────────────

def plan_sessions():
    """
    Plan which sessions to run today.
    Round-robin across clients, limited by free devices.
    Returns list of session dicts ready for execution.
    """
    clients = load_clients()
    assignments = load_assignments()
    free_devices = get_free_devices()

    if not free_devices:
        print("No free devices today.")
        return []

    # Build pool of remaining keywords across all clients (round-robin)
    remaining = []
    max_keywords = max(len(c["keywords"]) for c in clients)
    for i in range(max_keywords):
        for client in clients:
            if i < len(client["keywords"]):
                kw = client["keywords"][i]
                if not is_keyword_done_today(client["id"], kw):
                    remaining.append((client, kw))

    # Assign to free devices
    sessions = []
    device_iter = iter(free_devices)

    for client, keyword in remaining:
        try:
            device_id = next(device_iter)
        except StopIteration:
            break  # no more free devices

        # Check device is assigned to this client
        assigned = assignments.get(device_id, [])
        if client["id"] not in assigned:
            continue

        platform = random.choice(PLATFORMS)
        serial = DEVICE_POOL[device_id]["serial"]

        sessions.append({
            "client": client,
            "keyword": keyword,
            "platform": platform,
            "device_id": device_id,
            "serial": serial,
        })

    return sessions


# ── Main Commands ────────────────────────────────────────────────────────────

async def run_daily():
    """Generate prompts, distribute to devices, run in parallel."""
    sessions = plan_sessions()

    if not sessions:
        print("Nothing to run today. All devices used or all keywords done.")
        return

    print(f"\n🦞 AEO Daily Session — {date.today()}")
    print(f"📱 Free devices: {len(get_free_devices())}")
    print(f"📋 Sessions planned: {len(sessions)}")
    print("=" * 60)

    # Step 1: Generate prompts for each session
    print("\n📝 Generating prompts...")
    for sess in sessions:
        prompt, follow_up = generate_session_prompts(sess["client"], sess["keyword"])
        sess["prompt"] = prompt
        sess["follow_up"] = follow_up

        print(f"  {sess['client']['biz_name']} | {sess['keyword'][:40]}")
        print(f"    Prompt: {prompt[:80]}...")
        if follow_up:
            print(f"    Follow-up: {follow_up[:60]}...")

    # Step 2: Mark devices as used
    for sess in sessions:
        mark_device_used(sess["device_id"], sess["client"]["id"],
                         sess["keyword"], sess["platform"])

    # Step 3: Show plan
    print(f"\n{'='*60}")
    print(f"{'#':<3} {'Client':<25} {'Keyword':<30} {'Device':<12} {'Platform':<12} {'F/U'}")
    print(f"{'-'*3} {'-'*25} {'-'*30} {'-'*12} {'-'*12} {'-'*3}")
    for i, s in enumerate(sessions):
        fu = "Yes" if s["follow_up"] else "No"
        print(f"{i+1:<3} {s['client']['biz_name']:<25} {s['keyword'][:30]:<30} {s['device_id']:<12} {s['platform']:<12} {fu}")
    print(f"{'='*60}")

    # Step 4: Run all sessions in parallel
    print(f"\n▶️  Launching {len(sessions)} sessions in parallel...")
    results = await run_parallel(sessions)

    # Step 5: Update status and log
    for sess, result in zip(sessions, results):
        status = "success" if result.get("success") else "error"
        update_status(sess["device_id"], sess["client"]["id"], sess["keyword"], status)
        log_session(sess["client"], sess["keyword"], sess["platform"],
                    sess["prompt"], sess["follow_up"], sess["device_id"], status)

    # Summary
    success_count = sum(1 for r in results if r.get("success"))
    print(f"\n✅ Done: {success_count}/{len(results)} sessions succeeded")
    print(f"📋 Logs: {LOG_FILE}")


async def run_audit():
    """Run weekly ranking audit for all clients x keywords."""
    clients = load_clients()
    free_devices = get_free_devices()

    if not free_devices:
        print("No free devices for audit today.")
        return

    print(f"\n📊 Weekly Ranking Audit — {date.today()}")

    sessions = []
    device_iter = iter(free_devices)

    for client in clients:
        for keyword in client["keywords"]:
            if is_keyword_done_today(client["id"], keyword):
                continue
            try:
                device_id = next(device_iter)
            except StopIteration:
                break

            prompt = generate_ranking_prompt(client, keyword)
            serial = DEVICE_POOL[device_id]["serial"]
            platform = random.choice(PLATFORMS)

            sessions.append({
                "client": client,
                "keyword": keyword,
                "platform": platform,
                "device_id": device_id,
                "serial": serial,
                "prompt": prompt,
                "follow_up": None,
            })

            mark_device_used(device_id, client["id"], keyword, platform)

    if not sessions:
        print("Nothing to audit.")
        return

    print(f"📋 Audit sessions: {len(sessions)}")
    results = await run_parallel(sessions)

    for sess, result in zip(sessions, results):
        status = "success" if result.get("success") else "error"
        update_status(sess["device_id"], sess["client"]["id"], sess["keyword"], status)
        log_session(sess["client"], sess["keyword"], sess["platform"],
                    sess["prompt"], None, sess["device_id"], status)

    success_count = sum(1 for r in results if r.get("success"))
    print(f"\n✅ Audit done: {success_count}/{len(results)} succeeded")


def show_status():
    """Show today's device and keyword status."""
    clients = load_clients()
    today_rot = get_today_rotation()

    print(f"\n📊 Status — {date.today()}")
    print(f"{'='*60}")

    for device_id in DEVICE_POOL:
        device_sessions = today_rot.get(device_id, {})
        status_icon = "🔴" if not device_sessions else "🟢"
        print(f"\n{status_icon} {device_id} ({DEVICE_POOL[device_id]['serial']})")
        if device_sessions:
            for rot_key, info in device_sessions.items():
                print(f"   {info.get('keyword', rot_key)} | {info.get('platform')} | {info.get('status')}")
        else:
            print(f"   (free)")

    print(f"\n📋 Clients:")
    for c in clients:
        done = []
        for d in DEVICE_POOL:
            for rot_key in today_rot.get(d, {}):
                if rot_key.startswith(f"{c['id']}:"):
                    done.append(rot_key.split(":", 1)[1])
        remaining = [k for k in c["keywords"] if k not in done]
        print(f"  {c['biz_name']}: {len(done)} done, {len(remaining)} remaining")


def reset_rotation():
    """Reset today's rotation for testing."""
    today = str(date.today())
    save_rotation({today: {d: {} for d in DEVICE_POOL}})
    print(f"🔄 Rotation reset for {today}")


# ── Entry Point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AEO DroidRun Orchestrator v6.0")
    parser.add_argument("--audit", action="store_true", help="Run weekly ranking audit")
    parser.add_argument("--status", action="store_true", help="Show today's status")
    parser.add_argument("--reset", action="store_true", help="Reset today's rotation")
    args = parser.parse_args()

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("⚠️  DEEPSEEK_API_KEY not set!")
        print("   export DEEPSEEK_API_KEY='your-key-here'")
        sys.exit(1)

    if args.status:
        show_status()
    elif args.reset:
        reset_rotation()
    elif args.audit:
        asyncio.run(run_audit())
    else:
        asyncio.run(run_daily())


if __name__ == "__main__":
    main()
