"""
AEO Maestro Runner
------------------
Flask server that receives prompt from OpenClaw
and executes it on a real Android device using Maestro.


- One static YAML file — variables passed via --env flags
- Runs maestro in background thread → Flask responds immediately
- Human typing simulation via Maestro inputText (character delays)
- No file writing per session — clean and fast
"""


import json
import time
import random
import threading
import subprocess
from datetime import datetime
from flask import Flask, request, jsonify


app = Flask(__name__)
LOG_FILE      = "sessions_log.json"
YAML_FILE     = "aeo_session.yaml"
YAML_FOLLOWUP = "aeo_followup.yaml"


CLIENT = {
    "biz_name": "Mae's Childcare",
    "biz_url":  "https://www.maeschildcare.com",
    "city":     "San Francisco",
    "state":    "California"
}


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



# ── Maestro Session ───────────────────────────────────────────────────────────


def run_maestro_session(prompt, follow_up, platform,
                        keyword, device_id, proxy):
    print(f"\n{'='*60}")
    print(f"📱 Maestro session starting")
    print(f"🎯 Platform : {platform}")
    print(f"🔑 Keyword  : {keyword}")
    print(f"📝 Prompt   : {prompt[:80]}...")
    if follow_up:
        print(f"💬 Follow-up: {follow_up[:60]}...")
    print(f"{'='*60}")


    url    = PLATFORM_URLS.get(platform,    "https://gemini.google.com")
    name   = PLATFORM_NAMES.get(platform,   "Gemini AI")
    domain = PLATFORM_DOMAINS.get(platform, "gemini.google.com")
    title  = PLATFORM_TITLES.get(platform,  "Google Gemini")


    status = "success"


    try:
        # ── Run seeding prompt ────────────────────────────────────────────────
        print(f"\n▶️  Running maestro test — seeding prompt...")
        result = subprocess.run(
            [
                "maestro", "test", YAML_FILE,
                "--env", f"PLATFORM_URL={url}",
                "--env", f"PLATFORM_NAME={name}",
                "--env", f"PLATFORM_DOMAIN={domain}",
                "--env", f"PLATFORM_TITLE={title}",
                "--env", f"PROMPT={prompt}",
                "--env", f"FOLLOW_UP={follow_up or ''}",
                "--env", f"DEVICE_ID={device_id}"
            ],
            capture_output=True,
            text=True,
            timeout=120
        )


        if result.returncode != 0:
            print(f"❌ Maestro error:\n{result.stderr}")
            status = "error"
        else:
            print(f"✅ Seeding prompt session complete")
            print(result.stdout[-500:] if result.stdout else "")


        # ── Run follow-up if provided ─────────────────────────────────────────
        if follow_up and status == "success":
            wait = random.uniform(2.0, 5.0)
            print(f"\n⏳ Waiting {wait:.1f}s before follow-up...")
            time.sleep(wait)


            print(f"▶️  Running maestro test — follow-up...")
            result_fu = subprocess.run(
                [
                    "maestro", "test", YAML_FOLLOWUP,
                    "--env", f"PROMPT={follow_up}",
                    "--env", f"DEVICE_ID={device_id}"
                ],
                capture_output=True,
                text=True,
                timeout=120
            )


            if result_fu.returncode != 0:
                print(f"⚠️  Follow-up error:\n{result_fu.stderr}")
            else:
                print(f"✅ Follow-up session complete")


    except subprocess.TimeoutExpired:
        print(f"⏱️  Maestro timed out after 120s")
        status = "timeout"


    except FileNotFoundError:
        print(f"❌ maestro command not found — is Maestro installed?")
        print(f"   Install: curl -Ls 'https://get.maestro.mobile.dev' | bash")
        status = "error_maestro_not_found"


    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        status = "error"


    log_entry(keyword, platform, prompt, follow_up, device_id, proxy, status)



def log_entry(keyword, platform, prompt, follow_up,
              device_id, proxy, status):
    entry = {
        "timestamp":   datetime.utcnow().isoformat() + "Z",
        "client_name": CLIENT["biz_name"],
        "keyword":     keyword,
        "platform":    platform,
        "prompt":      prompt,
        "follow_up":   follow_up,
        "device_id":   device_id,
        "proxy":       proxy,
        "status":      status,
        "runner":      "maestro"
    }
    try:
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)
    except:
        logs = []
    logs.append(entry)
    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)
    print(f"📋 Logged → {LOG_FILE}")



# ── Routes ────────────────────────────────────────────────────────────────────


@app.route("/run-aeo", methods=["POST"])
def run_aeo():
    data = request.get_json()


    if not data or "prompt" not in data:
        return jsonify({"error": "Missing prompt"}), 400


    prompt    = data.get("prompt")
    follow_up = data.get("follow_up", None)
    platform  = data.get("platform", "Gemini")
    keyword   = data.get("keyword", "unknown")
    device_id = data.get("device_id", "device-001")
    proxy     = data.get("proxy", "decodo-mock-001")


    print(f"\n🦞 OpenClaw triggered AEO session")
    print(f"   Client  : {CLIENT['biz_name']}")
    print(f"   Keyword : {keyword}")
    print(f"   Platform: {platform}")
    print(f"   Proxy   : {proxy}")


    t = threading.Thread(
        target=run_maestro_session,
        args=(prompt, follow_up, platform, keyword, device_id, proxy),
        daemon=True
    )
    t.start()


    return jsonify({
        "status":         "started",
        "message":        f"AEO session started for {CLIENT['biz_name']} — Maestro running on device",
        "keyword":        keyword,
        "platform":       platform,
        "prompt_preview": prompt[:100]
    })



@app.route("/logs", methods=["GET"])
def get_logs():
    try:
        with open(LOG_FILE, "r") as f:
            return jsonify(json.load(f))
    except:
        return jsonify([])



@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "runner": "maestro",
        "client": CLIENT["biz_name"],
        "yaml":   YAML_FILE
    })



# ── Main ──────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    print("\n🦞 AEO Maestro Runner")
    print("="*60)
    print(f"📋 Client  : {CLIENT['biz_name']} — {CLIENT['city']}, {CLIENT['state']}")
    print(f"📱 Runner  : Maestro (Android device)")
    print(f"📄 YAML    : {YAML_FILE} (static — variables passed via --env)")
    print(f"🤖 LLM     : OpenClaw sends prompt — Maestro executes on device")
    print(f"⚡ Endpoint: http://localhost:5001/run-aeo")
    print("="*60)
    print("\nWaiting for OpenClaw...\n")
    app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)