# AEO Appium — Android AEO Automation

Pure scripted UI automation using Appium + UiAutomator2 with WebView context switching for TRUE parallel execution across multiple Android devices. OpenClaw generates prompts, this server distributes and executes them on devices.

---

## Tested & Working Devices

| Brand | Model | Serial | Notes |
|-------|-------|--------|-------|
| Realme | RMX3930 | 0B64C27G22100FA1 | Works perfectly |
| Vivo | V2430 | 10HFBBFEBZ000RA | Works perfectly |
| Nubia | Z2460 | 324651961440 | Works perfectly |
| Samsung | SM-A075F | R83L103VCVH | Works perfectly |
| Samsung | SM-A075F | R83L112EVWK | Works perfectly |

### Known Incompatible Devices

| Brand | Model | Serial | Issue |
|-------|-------|--------|-------|
| Infinix | X6725 | 1490455613010287 | UiAutomator2 hangs on session creation |
| Xiaomi/Redmi | 25078RA3EA | G6TGT4SCTCYTDMDU | USB debugging (Security Settings) blocked |
| OPPO | CPH2773 | c0897ffc | WRITE_SECURE_SETTINGS permission denied |

---

## Architecture

```
aeo-appium/
├── main.py               # CLI orchestration (--test, --dry-run, --clients, --exclude)
├── server.py             # Flask API (port 5001) — OpenClaw sends prompts here
├── flows.py              # Appium flows: Gemini, ChatGPT, Perplexity (WebView context)
├── session_runner.py     # Parallel execution via threading (1 thread per device)
├── test_flows.py         # Test single device with hardcoded prompts (no LLM)
├── agents/
│   ├── prompt_generator.py   # DeepSeek prompt generation (fallback)
│   └── ranking_auditor.py    # Weekly audit prompt generation
├── setup_devices.py      # Dynamic device discovery + Appium health check
├── check_devices.py      # USB debugging security check
├── launch_scrcpy.sh      # Tile device screens with scrcpy
├── start_appium.sh       # Start Appium servers (1 per device, auto-discovers)
├── clients.json          # 13 clients, 5 keywords each (65 total)
├── active_devices.json   # Auto-generated: healthy devices with port assignments
├── device_rotation.json  # Auto-generated: tracks daily keyword completion
├── sessions_log.json     # Auto-generated: full session history
└── requirements.txt
```

---

## How It Works

### Two Systems

```
OpenClaw (cloud/other machine)          This Mac Mini (runner)
─────────────────────────────           ─────────────────────────
1. GET /clients                    →    Returns client data + keyword status
2. Generates prompts (LLM)
3. POST /run-all {sessions}        →    Distributes to devices
                                        Runs in TRUE parallel
                                        Logs results
4. Check /status, /logs/today      →    Returns progress
```

### WebView Context Switching

Each flow uses two Appium contexts:
- **NATIVE_APP** — Chrome first-run dialogs (FRE), address bar navigation
- **WEBVIEW_chrome** — web page interaction using real CSS selectors

This gives proper DOM access: `#prompt-textarea`, `button[aria-label='Submit']`, etc.

### Rotation Rules

```
1 device + 1 client = 1 keyword per day
```

- After running Client A's keyword, the device moves to Client B
- Each device serves multiple clients (1 keyword each, sequential)
- Each keyword runs on exactly 1 device per day (no duplicates)
- All devices run simultaneously (TRUE parallel)

Example with 5 devices, 3 clients (5 keywords each):
```
Device 1: C1-KW1 → C2-KW1 → C3-KW1  (3 sessions, sequential)
Device 2: C1-KW2 → C2-KW2 → C3-KW2  (3 sessions, sequential)
Device 3: C1-KW3 → C2-KW3 → C3-KW3  (3 sessions, sequential)
Device 4: C1-KW4 → C2-KW4 → C3-KW4
Device 5: C1-KW5 → C2-KW5 → C3-KW5
= all 15 keywords in 1 day
```

### Generation Wait

Flows detect when the AI finishes generating by polling for the "Stop" button to disappear. This prevents the follow-up from hitting "Stop generating" instead of "Send".

### Chrome First-Run Experience (FRE)

Every session clears Chrome (`pm clear`), which triggers FRE dialogs. The flow handles:
1. "Use without an account" / "Stay signed out" → `id/signin_fre_dismiss_button`
2. "No thanks" (notifications) → `id/negative_button`
3. "Got it" (Enhanced ad privacy) → `id/ack_button`
4. "Accept & continue" / "OK" / "Continue" → various

All devices are locked to portrait orientation via ADB before each session.

---

## Fresh Mac Mini Setup (Step by Step)

### 1. Install Prerequisites

```bash
# Install Homebrew (if not installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Node.js (required for Appium)
brew install node

# Install ADB (Android platform tools)
brew install --cask android-platform-tools

# Install Appium + UiAutomator2 driver
npm install -g appium
appium driver install uiautomator2

# Install scrcpy (optional — for viewing device screens)
brew install scrcpy

# Install Cloudflare tunnel (for OpenClaw to reach the server)
brew install cloudflared

# Install Python dependencies
cd /Users/seolocalph/projects/aeo-appium
pip3 install -r requirements.txt
```

### 2. Set Environment Variables

Add to `~/.zshrc`:
```bash
echo 'export ANDROID_HOME=/opt/homebrew/Caskroom/android-platform-tools/36.0.2' >> ~/.zshrc
source ~/.zshrc
```

Verify:
```bash
echo $ANDROID_HOME    # should print the path
adb version           # should work
appium --version      # should work
```

### 3. Connect Android Devices

1. Enable **Developer Options** on each phone (tap Build Number 7 times)
2. Enable **USB Debugging**
3. Enable **USB Debugging (Security Settings)** — the SECOND toggle:
   - Xiaomi/MIUI: Settings → Additional Settings → Developer Options
   - OPPO/Realme: Settings → Additional Settings → Developer Options → "Disable permission monitoring"
   - Samsung: Settings → Developer Options → toggle USB debugging off/on → Reboot
   - Vivo: Settings → Developer Options → "USB debugging (Security Settings)"
4. Connect via USB or wireless ADB
5. Verify: `adb devices` should list all phones

### 4. Check Devices

```bash
cd /Users/seolocalph/projects/aeo-appium

# Quick USB debugging check on all connected devices
python3 check_devices.py

# Fix any "NEEDS FIX" devices before proceeding
```

---

## Daily Startup (3 Terminals)

### Terminal 1 — Appium Servers

```bash
cd /Users/seolocalph/projects/aeo-appium
bash start_appium.sh
```

This auto-discovers ALL connected devices from `adb devices` and starts one Appium server per device on sequential ports (4723, 4724, ...). Each server gets `--allow-insecure uiautomator2:chromedriver_autodownload` for WebView support.

### Terminal 2 — Assign Devices + Start API

```bash
cd /Users/seolocalph/projects/aeo-appium

# Assign healthy devices (checks USB debug + Appium connection)
python3 setup_devices.py --assign

# Start the API server
python3 server.py
```

`setup_devices.py --assign` tests each device by creating an Appium session. Only devices that pass get written to `active_devices.json`. The server reads this file.

### Terminal 3 — Cloudflare Tunnel (if OpenClaw is remote)

```bash
cloudflared tunnel --url http://localhost:5001
```

Gives a public URL like `https://xyz.trycloudflare.com`. Update the OpenClaw SKILL.md `RUNNER_HOST` with this URL.

---

## Adding More Devices

1. Connect the new phone via USB or wireless ADB
2. Enable USB Debugging + Security Settings on the phone
3. Verify: `adb devices` shows it
4. Restart Appium servers: `bash start_appium.sh` (auto-discovers new device)
5. Re-assign: `python3 setup_devices.py --assign`
6. Restart `server.py`

The system automatically labels devices sequentially (device-001, device-002, ...) and assigns ports (4723, 4724, ...) based on the number connected. No code changes needed.

---

## Testing

### Test a Single Device (no LLM)

```bash
# Test all 3 platforms on the first connected device
python3 test_flows.py

# Test one platform
python3 test_flows.py --platform Gemini
python3 test_flows.py --platform ChatGPT
python3 test_flows.py --platform Perplexity

# Skip follow-up for faster testing
python3 test_flows.py --platform Gemini --no-follow-up

# Test a specific device
python3 test_flows.py --serial R83L103VCVH --port 4729
```

Uses hardcoded test prompts. No DeepSeek API key needed.

### Test Multiple Devices with Hardcoded Prompts (no LLM)

```bash
# Preview what will run (no execution)
python3 main.py --dry-run --test --clients 3

# Run test mode: hardcoded prompts, 3 clients, all devices
python3 main.py --test --clients 3

# Exclude a broken device
python3 main.py --test --clients 3 --exclude device-004

# Exclude multiple
python3 main.py --test --exclude device-003,device-004
```

### Run with Real DeepSeek Prompts

```bash
export DEEPSEEK_API_KEY="your-key"

# All 13 clients, all devices
python3 main.py

# Limit to first 5 clients
python3 main.py --clients 5
```

---

## CLI Flags

```
python3 main.py                                    # full run (all clients, DeepSeek)
python3 main.py --test                             # hardcoded prompts (no LLM)
python3 main.py --dry-run                          # preview session plan only
python3 main.py --clients 3                        # first 3 clients only
python3 main.py --exclude device-004               # skip specific device
python3 main.py --exclude device-003,device-004    # skip multiple
python3 main.py --status                           # today's status
python3 main.py --reset                            # reset today's rotation
python3 main.py --audit                            # weekly ranking audit
```

Combine flags: `python3 main.py --test --clients 3 --exclude device-004`

---

## API Endpoints (server.py — port 5001)

### POST /run-aeo
Single session. OpenClaw sends the prompt.
```bash
curl -X POST http://localhost:5001/run-aeo \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": 0,
    "keyword": "bilingual childcare San Francisco",
    "prompt": "I heard Maes Childcare near Alamo Square is solid...",
    "follow_up": "Cool, do they post updates on their profile?",
    "platform": "Gemini"
  }'
```

### POST /run-all
Batch sessions. OpenClaw sends all at once. Server distributes across devices.
```bash
curl -X POST http://localhost:5001/run-all \
  -H "Content-Type: application/json" \
  -d '{
    "sessions": [
      {"client_id": 0, "keyword": "...", "prompt": "...", "follow_up": "..."},
      {"client_id": 1, "keyword": "...", "prompt": "...", "follow_up": "..."}
    ]
  }'
```

### GET /clients
Client list with keyword status. **This is the single source of truth for OpenClaw.**
```bash
curl http://localhost:5001/clients
```

Returns: `total_clients`, `devices_total`, `total_remaining_keywords`, `total_remaining_slots`, and per-client `keywords_done`, `keywords_remaining`, `devices_available`.

### GET /status
Today's rotation — which devices ran which clients/keywords.

### GET /health
Connected devices + Appium server status.

### POST /reset
Reset today's rotation (for testing/re-running).

### GET /logs/today
Today's session logs.

---

## Platform Flows

### Gemini (gemini.google.com)
1. **[NATIVE]** Clear Chrome + lock portrait → launch → dismiss FRE → navigate via address bar
2. **[WEBVIEW]** Find input (`div[contenteditable]`) → type via `execCommand('selectAll') + execCommand('insertText')` → click Send (`button[aria-label='Send message']`)
3. **[WEBVIEW]** Wait for generation to finish (poll for stop button to disappear, up to 120s)
4. **[ADB]** Scroll: 12 swipes × 400px × 700ms, 2.2s pause between
5. **[WEBVIEW]** Follow-up (if any): find input → type → send → wait for generation → scroll

### ChatGPT (chatgpt.com)
1. **[NATIVE]** Clear Chrome + lock portrait → launch → dismiss FRE → navigate
2. **[WEBVIEW]** Find `#prompt-textarea` → type via React-aware native JS setter → click `#composer-submit-button`
3. **[WEBVIEW]** Wait for generation (poll stop button)
4. **[ADB]** Scroll
5. **[WEBVIEW]** Follow-up: same flow

### Perplexity (www.perplexity.ai)
1. **[NATIVE]** Clear Chrome + lock portrait → launch → dismiss FRE → navigate → dismiss Comet modals
2. **[WEBVIEW]** Dismiss remaining modals via JS → find `#ask-input` (contenteditable) → type via `execCommand` → JS click `button[aria-label='Submit']` (bypasses overlay)
3. **[WEBVIEW]** Wait for generation
4. **[ADB]** Scroll
5. **[WEBVIEW]** Follow-up: same flow

---

## OpenClaw Integration

OpenClaw generates prompts and sends them to this server. It does NOT know about devices, ports, or platforms.

### SKILL.md Location
```
~/.openclaw/workspace/skills/aeo/SKILL.md
```

### Key Setting
The SKILL.md has a configurable `RUNNER_HOST` at the top:
```
RUNNER_HOST=https://your-tunnel-url.trycloudflare.com
```

Change this to match your Cloudflare tunnel URL or local IP.

### Flow
1. OpenClaw calls `GET {RUNNER_HOST}/clients` — gets client data + remaining keywords
2. OpenClaw generates prompts for each remaining keyword (it IS the LLM)
3. OpenClaw sends `POST {RUNNER_HOST}/run-all` with all sessions
4. Server distributes, runs in parallel, logs results
5. OpenClaw checks `GET {RUNNER_HOST}/logs/today` for results

---

## Technical Details

| Feature | Implementation |
|---------|---------------|
| Parallelism | `threading.Thread` per session, `threading.Lock` per device |
| Device discovery | Dynamic from `adb devices`, no hardcoded serials |
| Port assignment | Sequential from 4723 based on sorted device order |
| WebView access | `driver.switch_to.context("WEBVIEW_chrome")` + CSS selectors |
| Text input (textarea) | React-aware native JS setter + `input` event dispatch |
| Text input (contenteditable) | `execCommand('selectAll')` + `execCommand('insertText')` |
| Button clicks | JS `element.click()` to bypass overlays |
| Generation wait | Poll for stop/streaming button to disappear (up to 120s, 3s intervals) |
| Scrolling | ADB subprocess: `input swipe 360 1100 360 700 700` × 12, 2.2s pause |
| Portrait lock | ADB `settings put system accelerometer_rotation 0` + `user_rotation 0` before each session |
| Chrome FRE | Handles: sign-in dismiss, notifications, ad privacy, accept & continue |
| Chromedriver | Auto-downloaded via `chromedriverAutodownload` capability |
| Appium flag | `--allow-insecure uiautomator2:chromedriver_autodownload` |
| Remote access | Cloudflare Tunnel (`cloudflared tunnel --url http://localhost:5001`) |

---

## Troubleshooting

### "ANDROID_HOME not set"
```bash
echo 'export ANDROID_HOME=/opt/homebrew/Caskroom/android-platform-tools/36.0.2' >> ~/.zshrc
source ~/.zshrc
```

### "No Chromedriver found"
Appium needs `--allow-insecure uiautomator2:chromedriver_autodownload`. Use `start_appium.sh` which includes this flag.

### Device stuck on "Enhanced ad privacy" dialog
The flow handles this with `id/ack_button` ("Got it"). If still stuck, the device may have a UiAutomator2 compatibility issue. Check Appium logs: `cat /tmp/appium-device-XXX.log`

### Device goes landscape
Portrait is locked via ADB before each session. If still happens, manually: Settings → Display → Auto-rotate → OFF.

### Only 1 device running
Each device needs its own Appium server. Run `bash start_appium.sh` (starts one per connected device).

### "0 devices assigned"
Run `bash start_appium.sh` FIRST, then `python3 setup_devices.py --assign`. Appium servers must be running before assign.

### active_devices.json is empty `{}`
Delete it and restart: `rm active_devices.json && bash start_appium.sh && python3 setup_devices.py --assign`

### Appium session creation hangs
Some budget phones (Infinix, some TECNO) can't run UiAutomator2. Disconnect them: `adb disconnect <serial>` and remove from `active_devices.json`.

### USB debugging security check fails
On the device: Settings → Developer Options → enable "USB debugging (Security Settings)" (the SECOND toggle). Reboot after enabling.

### OPPO "Permission denial"
On OPPO devices: Settings → Additional Settings → Developer Options → "Disable permission monitoring" → Enable → Reboot.

### Reset stuck rotation
```bash
python3 main.py --reset
# or via API:
curl -X POST http://localhost:5001/reset
```

### Cloudflare tunnel URL changed
The free tunnel URL changes on each restart. Update `RUNNER_HOST` in the OpenClaw SKILL.md. For a permanent URL, create a free Cloudflare account and set up a named tunnel.

---

## Quick Reference

```bash
# Daily startup
bash start_appium.sh                    # Terminal 1: Appium servers
python3 setup_devices.py --assign       # Assign healthy devices
python3 server.py                       # Terminal 2: API server
cloudflared tunnel --url http://localhost:5001  # Terminal 3: tunnel

# Testing
python3 test_flows.py --platform Gemini         # single device test
python3 main.py --test --clients 3              # multi-device test
python3 main.py --dry-run --clients 3           # preview plan

# Operations
python3 main.py --status                        # today's status
python3 main.py --reset                         # reset rotation
curl http://localhost:5001/health               # API health
curl http://localhost:5001/clients              # client status
curl http://localhost:5001/logs/today           # today's logs

# Adding devices
# 1. Connect phone + enable USB debugging
# 2. bash start_appium.sh
# 3. python3 setup_devices.py --assign
# 4. Restart server.py
```
