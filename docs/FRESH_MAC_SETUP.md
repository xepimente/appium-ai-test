# Fresh Mac Mini Setup — AEO Executor

Step-by-step for bringing up the AEO executor service on a brand-new Mac. End
state: an HTTP executor on port 8100 that the scheduler can POST Jobs to, with
a pool of connected Android phones ready to run sessions.

If the scheduler is remote, add a tunnel at the end (§9).

---

## What runs on this Mac

```
┌────────────────────────────────────────────────────────────────────┐
│  Mac Mini                                                          │
│                                                                    │
│   Scheduler ───HTTP:8100──▶  aeo_executor (FastAPI)                │
│                                    │                               │
│                                    ▼                               │
│                               session_runner                       │
│                                    │                               │
│                    ┌───────────────┼─────────────────┐             │
│                    ▼               ▼                 ▼             │
│              Device Manager    gost (per-Job)    ADB commands      │
│              (localhost:8080)  (port 11001+)     to phones         │
│                    │               │                 │             │
│                    │               └──▶ Decodo       │             │
│                    ▼                                 ▼             │
│              phone (SocksDroid)                 phone (Chrome)     │
└────────────────────────────────────────────────────────────────────┘
```

---

## 1. Install prerequisites

```bash
# Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Android Platform Tools (adb)
brew install --cask android-platform-tools

# gost (SOCKS5 multiplexer — per-Job proxy listener)
brew install gost

# Java (for Device Manager)
brew install openjdk@17

# Python deps
pip3 install fastapi uvicorn pydantic requests appium-python-client websocket-client

# Optional — scrcpy for viewing phone screens during debugging
brew install scrcpy

# Optional — cloudflared if scheduler is remote
brew install cloudflared
```

Set `ANDROID_HOME` (adb + tools):
```bash
echo 'export ANDROID_HOME=/opt/homebrew/Caskroom/android-platform-tools/36.0.2' >> ~/.zshrc
echo 'export PATH=$ANDROID_HOME/platform-tools:$PATH' >> ~/.zshrc
source ~/.zshrc
```

---

## 2. Clone the repo

```bash
git clone <repo-url> ~/projects/aeo-appium
cd ~/projects/aeo-appium
```

---

## 3. Create `.env`

Contains Decodo proxy credentials. **Required** — `gost_manager.py` refuses to
start without `PROXY_PASSWORD`.

```bash
cat > .env <<'EOF'
PROXY_HOST=gate.decodo.com
PROXY_PORT=10001
PROXY_BASE_USER=user-spknlt0736
PROXY_PASSWORD=<your-decodo-password>
DEVICE_MANAGER_URL=http://localhost:8080
EOF
chmod 600 .env
```

Copy `PROXY_PASSWORD` from the current Mac's `.env` or get it from 1Password /
your secret store.

---

## 4. Install Device Manager (Java service)

The device-manager is a separate repo — clone and build it.

```bash
git clone <device-manager-repo> ~/app/device-manager
cd ~/app/device-manager
# Build the jar (if not pre-built)
./mvnw clean package
# The jar lives at:
ls ~/app/device-manager/lib/device-manager-0.0.1-SNAPSHOT.jar
```

Start it (keep this terminal open, or launch under `launchctl` / `nohup`):
```bash
cd ~/app/device-manager
java -jar ./lib/device-manager-0.0.1-SNAPSHOT.jar
```

Health check:
```bash
curl -sf http://localhost:8080/device/list && echo "Device Manager: UP"
```

---

## 5. Connect Android phones

### 5.1 Per-phone one-time setup

1. Developer Options → **USB Debugging** ON
2. Developer Options → **USB Debugging (Security Settings)** ON ← the second toggle
3. Install **SocksDroid** app (`conf/apk/socksdroid/base.apk` from the device-manager repo)
4. Connect via USB the first time to accept the RSA key, then you can go wireless
5. Brand-specific:
   - **Infinix / TECNO** — usually just works after the above
   - **OPPO** — enable "**Disable Permission Monitoring**" in Developer Options
   - **Redmi** — sign in with Mi Account for USB debugging (Security Settings)

### 5.2 Wireless ADB (optional but recommended for stable mounts)

Phone needs to be on the same Wi-Fi as the Mac.
```bash
adb pair <phone-ip>:<pair-port>         # one-time
adb connect <phone-ip>:<debug-port>
adb devices                              # should show the phone
```

### 5.3 Assign devices to the pool

```bash
cd ~/projects/aeo-appium
python3 setup_devices.py --assign
cat active_devices.json      # confirm all phones listed
```

Each phone gets an entry like:
```json
"device-101": {
  "serial": "adb-...",
  "port":   4731,
  "use_adb": true,
  "brand":  "Infinix"
}
```

If a phone is missing, run `adb devices` to verify it's online, then re-run
`setup_devices.py --assign`.

---

## 6. Start Appium servers (only if using non-ADB brands)

Infinix and TECNO phones are ADB-only and do NOT need Appium. If the pool has
Samsung / OPPO / Realme / Vivo / Nubia, start Appium servers:

```bash
# Install Appium globally (one-time)
npm install -g appium
appium driver install uiautomator2

# Then each day:
cd ~/projects/aeo-appium
bash start_appium.sh
```

One Appium server per phone, ports starting at 4723. If the pool is all
Infinix/TECNO (like today's setup), skip this step entirely.

---

## 7. Start the executor

```bash
cd ~/projects/aeo-appium
python3 -m aeo_executor
```

Listens on `0.0.0.0:8100`. Logs go to stdout.

For production / always-on, run under `launchctl` or `nohup`:
```bash
nohup python3 -m aeo_executor > /tmp/aeo_executor.log 2>&1 &
```

Health check:
```bash
curl -s http://localhost:8100/health | python3 -m json.tool
```

Expected:
```json
{
  "status": "healthy",
  "version": "2.1.0",
  "adb_devices_connected": 5,
  "device_pool_size": 5,
  "in_flight": 0,
  "available": 5
}
```

---

## 8. Smoke-test from the Mac itself

```bash
# Verify gost + Decodo work
python3 gost_manager.py test

# Post a no-proxy Job (fastest smoke)
curl -sS -X POST http://localhost:8100/v1/jobs \
  -H 'Content-Type: application/json' \
  --max-time 600 \
  -d '{
    "job_id":       "smoke-001",
    "client_id":    1,
    "business_id":  1,
    "keyword_id":   1,
    "keyword_text": "test",
    "platform":     "Perplexity",
    "prompt":       "What is 2+2?",
    "device_id":    "device-101"
  }' | python3 -m json.tool
```

Expect `status: "success"` in ~3 min with a `response_preview`.

---

## 9. Expose to remote scheduler (only if scheduler is not on the LAN)

### Option A — Cloudflare Tunnel (recommended)

```bash
cloudflared tunnel --url http://localhost:8100
# prints a URL like https://xyz-abc.trycloudflare.com
# give that URL to the scheduler team
```

### Option B — LAN-only (if scheduler is on the same network)

```bash
ipconfig getifaddr en0         # e.g. 192.168.0.102
# scheduler POSTs to http://192.168.0.102:8100/v1/jobs
```

Make sure macOS firewall allows incoming on 8100:
```
System Settings → Network → Firewall → Options → Allow Python
```

---

## 10. Daily checklist

Before accepting Jobs from the scheduler each day:

```bash
# From ~/projects/aeo-appium
curl -sf http://localhost:8080/device/list >/dev/null && echo "device-manager: UP" || echo "device-manager: DOWN"
curl -sf http://localhost:8100/health      >/dev/null && echo "executor:       UP" || echo "executor:       DOWN"
adb devices
cat active_devices.json | python3 -m json.tool | head -20
```

If a device is offline, power-cycle the phone or re-run `adb connect`, then
`python3 setup_devices.py --assign`.

---

## 11. Common failure modes

| Symptom | Fix |
|---|---|
| `503 active_devices.json is empty` | Run `python3 setup_devices.py --assign` |
| `PROXY_PASSWORD is not set` | `.env` missing or not readable — fix §3 |
| `Device Manager unreachable` in logs | Device Manager not running — fix §4 |
| `gost binary not found` | `brew install gost` |
| `409 device_busy` for every Job | Stale in-flight state — restart executor |
| Port 8100 already in use | `lsof -nP -iTCP:8100 -sTCP:LISTEN` → kill stale process |
| VPN key icon never appears on phone | SocksDroid not paired with Device Manager, or phone lost VPN permission — `adb shell appops set net.typeblog.socks ACTIVATE_VPN allow` |

---

## 12. File / port cheat sheet

| What | Where | Port |
|---|---|---|
| AEO executor | `python3 -m aeo_executor` | 8100 |
| Device Manager | `~/app/device-manager/...jar` | 8080 |
| gost listeners (per-Job) | spun up by executor | 11001–12000 |
| Appium servers (per-phone) | `bash start_appium.sh` | 4723+ |
| Pool config | `~/projects/aeo-appium/active_devices.json` | — |
| Proxy creds | `~/projects/aeo-appium/.env` | — |
| Executor logs | stdout (or `/tmp/aeo_executor.log` if `nohup`'d) | — |

---

## 13. Related docs

- `docs/EXECUTOR_PAYLOAD.md` — Job / JobResult schemas for the scheduler
- `docs/MANUAL_RUN.md` — operator runbook (manual runs, teardown, gost details)
- `README.md` — legacy architecture overview (still useful for platform flows)
