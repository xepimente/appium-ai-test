# AEO Appium — Android AEO Automation

Hybrid mobile automation for AEO (Answer Engine Optimization) sessions across multiple Android devices. Uses **Appium + WebView context switching** for compatible devices and **ADB-only automation** for budget phones (Infinix, TECNO). OpenClaw generates prompts, this server distributes and executes them on devices in TRUE parallel.

---

## Tested & Working Devices

### Appium Mode (WebView context switching)

| Brand | Model | Serial | Notes |
|-------|-------|--------|-------|
| Realme | RMX3930 | 0B64C27G22100FA1 | Works perfectly |
| Vivo | V2430 | 10HFBBFEBZ000RA | Works perfectly |
| Nubia | Z2460 | 324651961440 | Works perfectly |
| Samsung | SM-A075F | R83L103VCVH | Works perfectly |
| Samsung | SM-A075F | R83L112EVWK | Works perfectly |

### ADB-Only Mode (budget phones)

| Brand | Model | Notes |
|-------|-------|-------|
| Infinix | X6725 | 7 devices tested, all working via ADB-only |
| TECNO | KM4k | Auto-detected, uses ADB-only |

### Known Incompatible Devices

| Brand | Model | Issue |
|-------|-------|-------|
| Xiaomi/Redmi | 25078RA3EA | USB debugging (Security Settings) blocked |
| OPPO | CPH2773 | WRITE_SECURE_SETTINGS permission denied |

---

## Architecture

```
aeo-appium/
├── main.py               # CLI orchestration (--test, --dry-run, --clients, --exclude)
├── server.py             # Flask API (port 5001) — OpenClaw sends prompts here
├── flows.py              # Appium flows: Gemini, ChatGPT, Perplexity (WebView context)
├── flows_adb.py          # ADB-only flows: same platforms, no Appium needed
├── session_runner.py     # Hybrid parallel runner — auto-picks Appium or ADB per device
├── test_flows.py         # Test Appium flows on single device (no LLM)
├── test_adb_flows.py     # Test ADB flows on single device (no LLM)
├── agents/
│   ├── prompt_generator.py   # DeepSeek prompt generation (fallback)
│   └── ranking_auditor.py    # Weekly audit prompt generation
├── setup_devices.py      # Dynamic device discovery + auto-detects Appium vs ADB mode
├── check_devices.py      # USB debugging security check
├── launch_scrcpy.sh      # Tile device screens with scrcpy
├── start_appium.sh       # Start Appium servers (1 per device, auto-discovers)
├── clients.json          # 13 clients, 5 keywords each (65 total)
├── active_devices.json   # Auto-generated: devices with port + mode (use_adb flag)
├── device_rotation.json  # Auto-generated: tracks daily keyword completion
├── sessions_log.json     # Auto-generated: full session history
└── requirements.txt
```

---

## How It Works

### Hybrid Automation

The system auto-detects each device's brand and picks the right automation backend:

| Brand | Mode | How it works |
|-------|------|-------------|
| Realme, Vivo, Samsung, Nubia | **Appium** | WebView context switching, CSS selectors, JS execution |
| Infinix, TECNO | **ADB-only** | `adb shell input tap/text`, `uiautomator dump`, coordinate-based |

This is transparent — same rotation logic, same parallel threading, same session logging. The device just runs a different automation engine.

### Two Systems

```
OpenClaw (cloud/other machine)          This Mac Mini (runner)
─────────────────────────────           ─────────────────────────
1. GET /clients                    →    Returns client data + keyword status
2. Generates prompts (LLM)
3. POST /run-all {sessions}        →    Distributes to devices
                                        Auto-picks Appium or ADB per device
                                        Runs in TRUE parallel
                                        Logs results
4. Check /status, /logs/today      →    Returns progress
```

### Rotation Rules

```
1 device + 1 client = 1 keyword per day
```

- After running Client A's keyword, the device moves to Client B
- Each device serves multiple clients (1 keyword each, sequential)
- Each keyword runs on exactly 1 device per day (no duplicates)
- All devices run simultaneously (TRUE parallel)

Example with 7 devices, 3 clients (5 keywords each):
```
Devices 1-3: C1-KW1, C2-KW1, C3-KW1  (1 session each)
Devices 4-6: C1-KW2, C2-KW2, C3-KW2  (1 session each)
Device  7:   C1-KW3                    (1 session)
= 7 keywords done in round 1, then devices continue with remaining keywords
= all 15 keywords done in 1 run
```

### Generation Wait

All flows detect when the AI finishes generating by polling for the "Stop" button to disappear. This prevents the follow-up from hitting "Stop generating" instead of "Send".

### Chrome First-Run Experience (FRE)

Every session clears Chrome (`pm clear`), which triggers FRE dialogs. Both Appium and ADB flows handle:
1. "Use without an account" / "Stay signed out"
2. "No thanks" (notifications)
3. "Got it" (Enhanced ad privacy)
4. "Accept & continue" / "OK" / "Continue"

All devices are locked to portrait orientation via ADB before each session.

---

## Fresh Mac Mini Setup

### 1. Install Prerequisites

```bash
# Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Node.js + Appium + UiAutomator2
brew install node
npm install -g appium
appium driver install uiautomator2

# ADB
brew install --cask android-platform-tools

# Python deps
pip3 install -r requirements.txt

# Optional
brew install scrcpy           # view device screens
brew install cloudflared      # tunnel for remote OpenClaw
```

### 2. Set Environment Variables

```bash
echo 'export ANDROID_HOME=/opt/homebrew/Caskroom/android-platform-tools/36.0.2' >> ~/.zshrc
source ~/.zshrc
```

### 3. Connect Android Devices

1. Enable **Developer Options** on each phone (tap Build Number 7 times)
2. Enable **USB Debugging**
3. Enable **USB Debugging (Security Settings)** — the SECOND toggle
4. Connect via USB or wireless ADB
5. Verify: `adb devices`

---

## Daily Startup (3 Terminals)

### Terminal 1 — Appium Servers

```bash
cd /Users/seolocalph/projects/aeo-appium
bash start_appium.sh
```

Auto-discovers ALL connected devices, starts one Appium server per device. Includes `--allow-insecure uiautomator2:chromedriver_autodownload` for WebView support. ADB-only devices (Infinix/TECNO) get servers too but won't use them.

### Terminal 2 — Assign Devices + Start API

```bash
python3 setup_devices.py --assign    # checks each device, writes active_devices.json
python3 server.py                    # start API on port 5001
```

`setup_devices.py` auto-detects device brand:
- Infinix/TECNO → marked as `use_adb: true` (skips Appium connection test)
- Others → tested with Appium connection

### Terminal 3 — Cloudflare Tunnel (if OpenClaw is remote)

```bash
cloudflared tunnel --url http://localhost:5001
```

---

## Adding More Devices

1. Connect phone via USB or wireless ADB
2. Enable USB Debugging + Security Settings
3. `adb devices` — verify it shows
4. `bash start_appium.sh` — restarts all Appium servers
5. `python3 setup_devices.py --assign` — re-assigns with new device
6. Restart `server.py`

The system auto-detects the brand and assigns the right mode. No code changes needed.

---

## Testing

### Test ADB-only flows (Infinix/TECNO)

```bash
# All 3 platforms on first device
python3 test_adb_flows.py

# One platform
python3 test_adb_flows.py --platform Gemini
python3 test_adb_flows.py --platform ChatGPT
python3 test_adb_flows.py --platform Perplexity

# Specific device
python3 test_adb_flows.py --serial adb-149145555W001028-XsQtPA._adb-tls-connect._tcp

# Skip follow-up
python3 test_adb_flows.py --platform Gemini --no-follow-up
```

### Test Appium flows (Realme/Vivo/Samsung/Nubia)

```bash
python3 test_flows.py --platform Gemini
python3 test_flows.py --platform ChatGPT
python3 test_flows.py --platform Perplexity
```

### Multi-device test with hardcoded prompts

```bash
python3 main.py --dry-run --test --clients 3      # preview plan
python3 main.py --test --clients 3                 # run test
python3 main.py --test --exclude device-002        # skip a device
```

### Real run with DeepSeek prompts

```bash
export DEEPSEEK_API_KEY="your-key"
python3 main.py --clients 5
python3 main.py                                    # all 13 clients
```

---

## CLI Flags

```
python3 main.py                                    # full run (all clients, DeepSeek)
python3 main.py --test                             # hardcoded prompts (no LLM)
python3 main.py --dry-run                          # preview session plan only
python3 main.py --clients 3                        # first 3 clients only
python3 main.py --exclude device-002               # skip specific device
python3 main.py --exclude device-002,device-005    # skip multiple
python3 main.py --status                           # today's status
python3 main.py --reset                            # reset today's rotation
python3 main.py --audit                            # weekly ranking audit
```

---

## API Endpoints (server.py — port 5001)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /run-aeo | Single session (OpenClaw sends prompt) |
| POST | /run-all | Batch sessions — distributes across devices |
| GET | /clients | Client data + keyword status (single source of truth for OpenClaw) |
| GET | /status | Today's rotation — which devices ran what |
| GET | /health | Device + Appium server status |
| POST | /reset | Reset today's rotation |
| GET | /logs/today | Today's session logs |
| GET | /logs | All logs (optional `?date=YYYY-MM-DD`) |

---

## Platform Flows

### Gemini (gemini.google.com)

**Appium mode:**
1. [NATIVE] Clear Chrome + lock portrait → dismiss FRE → navigate via address bar
2. [WEBVIEW] Find input (`div[contenteditable]`) → type via `execCommand('insertText')` → click Send
3. [WEBVIEW] Wait for generation (poll stop button)
4. [ADB] Scroll (left edge, x=15, avoids maps)
5. [WEBVIEW] Follow-up: same flow

**ADB mode:**
1. Clear Chrome → launch → dismiss FRE via `uiautomator dump` + tap
2. Navigate via address bar → type URL → Enter
3. Tap input area → type word by word via `adb input text` + `keyevent 62` (space)
4. Find Send via `uiautomator dump` (content-desc="Send message") → tap
5. Wait for generation → scroll → follow-up same flow

### ChatGPT (chatgpt.com)

**Appium mode:**
1. [NATIVE] Clear Chrome → dismiss FRE → navigate
2. [WEBVIEW] Find `#prompt-textarea` → type via React-aware JS setter → click `#composer-submit-button`
3. Wait → scroll → follow-up

**ADB mode:**
1. Clear Chrome → FRE → navigate
2. Find `prompt-textarea` via `uiautomator dump` → type → find `composer-submit-button` → tap
3. Wait → scroll → follow-up

### Perplexity (www.perplexity.ai)

**Appium mode:**
1. [NATIVE] Clear Chrome → FRE → navigate → dismiss Comet modals (native)
2. [WEBVIEW] Dismiss remaining modals via JS → find `#ask-input` → type via `execCommand` → JS click Submit
3. Wait → scroll → follow-up

**ADB mode:**
1. Clear Chrome → FRE → navigate
2. Dismiss Comet modals by tapping X at confirmed coordinates `(w*0.943, h*0.134)`
3. Find `ask-input` via `uiautomator dump` → type
4. Hide keyboard (tap content area) → poll until Submit button at `y > 1400` → tap
5. Wait → scroll → follow-up

---

## OpenClaw Integration

### SKILL.md Location
```
~/.openclaw/workspace/skills/aeo/SKILL.md
```

### Key Setting
```
RUNNER_HOST=https://your-tunnel-url.trycloudflare.com
```

### Flow
1. OpenClaw calls `GET {RUNNER_HOST}/clients` → gets client data + remaining keywords
2. Generates prompts for each remaining keyword
3. Sends `POST {RUNNER_HOST}/run-all` with all sessions
4. Server distributes, runs in parallel, logs results

---

## Technical Details

| Feature | Implementation |
|---------|---------------|
| Hybrid automation | Auto-detects brand → Appium (WebView) or ADB-only |
| ADB-only brands | Infinix, TECNO (configured in `setup_devices.py:ADB_ONLY_BRANDS`) |
| Parallelism | `threading.Thread` per session, `threading.Lock` per device |
| Device discovery | Dynamic from `adb devices`, no hardcoded serials |
| Port assignment | Sequential from 4723 based on sorted device order |
| WebView access | `driver.switch_to.context("WEBVIEW_chrome")` + CSS selectors |
| ADB text input | Word-by-word `adb input text` + `keyevent 62` (space) |
| ADB element finding | `uiautomator dump` + XML regex parsing |
| ADB Submit (Perplexity) | Hide keyboard → poll until Submit at `y > 1400` → tap |
| ADB Comet dismissal | Tap X at confirmed coordinates `(w*0.943, h*0.134)` |
| ADB scroll | Left edge `x=15` to avoid map widgets |
| Generation wait | Poll for stop/streaming button to disappear (up to 120s) |
| Portrait lock | ADB `accelerometer_rotation 0` + `user_rotation 0` |
| Chrome FRE | 6-attempt loop checking for each dialog via `uiautomator dump` |
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

### Device goes landscape
Portrait locked via ADB before each session. If still happens: Settings → Display → Auto-rotate → OFF.

### "uiautomator dump timed out"
Budget phones are slow — timeout is 20s. If still failing, the device may need a reboot.

### Perplexity Submit not clicking
The keyboard takes up to 10s to hide on Infinix phones. The code polls until Submit is at `y > 1400` (up to 15 attempts). If still failing, check pointer location coordinates.

### Scroll hitting map widget
Scroll uses `x=15` (left edge). If maps are full-width, this may still hit them — unavoidable for some responses.

### "by by by" garbage in follow-up (Gemini)
Caused by scroll swiping on the keyboard. Fixed by hiding keyboard before scrolling (`adb_scroll` calls `hide_keyboard` first).

### Comet modal not dismissing (Perplexity)
X button coordinates confirmed at `(w*0.943, h*0.134)` on 720x1600 Infinix. Use pointer location (`adb shell settings put system pointer_location 1`) to verify on different screen sizes.

### Enable pointer location (for debugging tap positions)
```bash
adb -s <serial> shell settings put system pointer_location 1    # ON
adb -s <serial> shell settings put system pointer_location 0    # OFF
```

### Only 1 device running
Each device needs its own Appium server. Run `bash start_appium.sh`.

### "0 devices assigned"
Run `bash start_appium.sh` FIRST, then `python3 setup_devices.py --assign`.

### active_devices.json is empty
```bash
rm active_devices.json && bash start_appium.sh && python3 setup_devices.py --assign
```

### Reset rotation
```bash
python3 main.py --reset
```

---

## Quick Reference

```bash
# Daily startup
bash start_appium.sh                    # Terminal 1: Appium servers
python3 setup_devices.py --assign       # Assign devices (auto-detects mode)
python3 server.py                       # Terminal 2: API server
cloudflared tunnel --url http://localhost:5001  # Terminal 3: tunnel

# Testing
python3 test_adb_flows.py --platform Gemini     # ADB test (Infinix)
python3 test_flows.py --platform Gemini          # Appium test (others)
python3 main.py --test --clients 3               # multi-device test
python3 main.py --dry-run --clients 3            # preview plan

# Operations
python3 main.py --status                         # today's status
python3 main.py --reset                          # reset rotation
curl http://localhost:5001/health                # API health
curl http://localhost:5001/clients               # client status

# Adding devices — just plug in and:
bash start_appium.sh
python3 setup_devices.py --assign
# restart server.py

# Debug tap positions
adb -s <serial> shell settings put system pointer_location 1
```
