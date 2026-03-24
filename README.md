# AEO Appium — Android AEO Automation

Hybrid mobile automation for AEO (Answer Engine Optimization) sessions across multiple Android devices. Uses **Appium + WebView context switching** for compatible devices and **ADB-only automation** for budget phones (Infinix, TECNO). OpenClaw generates prompts, this server distributes and executes them on devices in TRUE parallel.

Includes a **Ranking Audit system** that queries AI platforms with ranking prompts, captures screenshots of the response, and saves results locally (ready for S3 upload).

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
├── audit.py              # Ranking audit system — screenshot + text capture
├── screenshot.py         # CDP scroll + ADB screencap module
├── flows.py              # Appium flows: Gemini, ChatGPT, Perplexity (WebView context)
├── flows_adb.py          # ADB-only flows: same platforms, no Appium needed
├── session_runner.py     # Hybrid parallel runner — auto-picks Appium or ADB per device
├── test_flows.py         # Test Appium flows on single device (no LLM)
├── test_adb_flows.py     # Test ADB flows on single device (no LLM)
├── test_audit_gemini.py  # Test audit flow on Gemini (ADB or Appium)
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
├── audit_results/        # Auto-generated: audit screenshots + text + logs
│   ├── Gemini/           #   Screenshots per platform
│   ├── ChatGPT/
│   ├── Perplexity/
│   ├── text/             #   Response text files
│   └── audit_log.json    #   Audit run history
└── requirements.txt
```

---

## How It Works

### Two Systems: Seeding & Audit

```
┌─────────────────────────────────────────────────────────┐
│ SYSTEM 1: Daily Seeding Sessions                        │
│ Purpose: Build AI visibility (prompt + follow-up)       │
│ Script:  main.py / server.py (POST /run-all)            │
│ Mode:    Hybrid (Appium or ADB per device)              │
│ Output:  Session log (pass/fail)                        │
│ Freq:    Daily                                          │
├─────────────────────────────────────────────────────────┤
│ SYSTEM 2: Ranking Audit                                 │
│ Purpose: Check ranking position (screenshot + text)     │
│ Script:  audit.py / server.py (POST /audit)             │
│ Mode:    ADB-only (all devices, no Appium needed)       │
│ Output:  Screenshot + response text per platform        │
│ Freq:    Weekly                                         │
└─────────────────────────────────────────────────────────┘
```

### Hybrid Automation (Seeding)

The system auto-detects each device's brand and picks the right automation backend:

| Brand | Mode | How it works |
|-------|------|-------------|
| Realme, Vivo, Samsung, Nubia | **Appium** | WebView context switching, CSS selectors, JS execution |
| Infinix, TECNO | **ADB-only** | `adb shell input tap/text`, `uiautomator dump`, coordinate-based |

### Ranking Audit

The audit system queries AI platforms with a ranking prompt, then captures the response:

1. Sends concise ranking prompt: "Top 3 businesses for {keyword} in {city}, {state}..."
2. Waits for AI to finish generating
3. Uses **CDP** (Chrome DevTools Protocol) to scroll the response to the top of the viewport
4. Takes a single **ADB screenshot** — captures only the AI response (no user prompt)
5. Extracts response text via CDP JavaScript
6. Saves screenshot + text + metadata locally

```
audit_results/
├── Gemini/
│   └── 0_bilingual-childcare_20260325_083000.png
├── ChatGPT/
│   └── 0_bilingual-childcare_20260325_083100.png
├── Perplexity/
│   └── 0_bilingual-childcare_20260325_083200.png
├── text/
│   └── 0_bilingual-childcare_20260325_083000_Gemini.txt
└── audit_log.json
```

File naming: `{client_id}_{keyword-slug}_{timestamp}.png`

### OpenClaw Integration

```
OpenClaw (cloud/other machine)          This Mac Mini (runner)
─────────────────────────────           ─────────────────────────
1. GET /clients                    →    Returns client data + keyword status
2. Generates prompts (LLM)
3. POST /run-all {sessions}        →    Distributes to devices (seeding)
   POST /audit {platform, ...}     →    Runs ranking audit (all devices)
4. Check /status, /audit/status    →    Returns progress
```

### Rotation Rules (Seeding)

```
1 device + 1 client = 1 keyword per day
```

- After running Client A's keyword, the device moves to Client B
- Each device serves multiple clients (1 keyword each, sequential)
- Each keyword runs on exactly 1 device per day (no duplicates)
- All devices run simultaneously (TRUE parallel)

### Generation Wait

All flows detect when the AI finishes generating by polling for the "Stop" button to disappear. This prevents the follow-up from hitting "Stop generating" instead of "Send".

### Chrome First-Run Experience (FRE)

Every session clears Chrome (`pm clear`), which triggers FRE dialogs. Both Appium and ADB flows handle:
1. "Use without an account" / "Stay signed out"
2. "No thanks" (notifications)
3. "Got it" (Enhanced ad privacy)
4. "Accept & continue" / "OK" / "Continue"

Audit flows also handle:
5. Microphone permission ("Never allow" / "Block")
6. Gemini app banner (ADB coordinate tap)
7. Perplexity Comet modal (coordinate tap)

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

### Test Ranking Audit

```bash
# Single device, single platform
python3 audit.py --test --platform Gemini

# Single device, all 3 platforms
python3 audit.py --test

# All devices in parallel, single platform
python3 audit.py --test --platform Gemini --all-devices --exclude Redmi

# All devices, all platforms
python3 audit.py --test --all-devices --exclude Redmi

# Specific device
python3 audit.py --test --platform Gemini --serial <serial>
```

### Multi-device seeding test

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

### Seeding (main.py)

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

### Ranking Audit (audit.py)

```
python3 audit.py --test                            # test client, all platforms, 1 device
python3 audit.py --test --platform Gemini          # test client, Gemini only
python3 audit.py --test --all-devices              # test client, all devices parallel
python3 audit.py --all-devices --exclude Redmi     # all clients, exclude Redmi
python3 audit.py --clients 3 --platform ChatGPT   # first 3 clients, ChatGPT only
python3 audit.py --all-devices --exclude Redmi     # production run
```

---

## API Endpoints (server.py — port 5001)

### Seeding

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

### Ranking Audit

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /audit | Trigger ranking audit on all devices (parallel, background) |
| GET | /audit/status | Today's audit results from `audit_log.json` |

**POST /audit body (all optional):**
```json
{
  "platform": "Gemini",
  "clients": 3,
  "exclude": "Redmi,25078",
  "keyword_index": 0
}
```

**Example:**
```bash
# Trigger audit via API
curl -X POST http://localhost:5001/audit \
  -H "Content-Type: application/json" \
  -d '{"platform": "Gemini", "exclude": "Redmi"}'

# Check results
curl http://localhost:5001/audit/status
```

---

## Platform Flows

### Gemini (gemini.google.com)

**Appium mode (seeding):**
1. [NATIVE] Clear Chrome + lock portrait → dismiss FRE → navigate via address bar
2. [WEBVIEW] Find input (`div[contenteditable]`) → type via `execCommand('insertText')` → click Send
3. [WEBVIEW] Wait for generation (poll stop button)
4. [ADB] Scroll (left edge, x=15, avoids maps)
5. [WEBVIEW] Follow-up: same flow

**ADB mode (seeding):**
1. Clear Chrome → launch → dismiss FRE via `uiautomator dump` + tap
2. Navigate via address bar → type URL → Enter
3. Tap input area → type word by word via `adb input text` + `keyevent 62` (space)
4. Find Send via `uiautomator dump` (content-desc="Send message") → tap
5. Wait for generation → scroll → follow-up same flow

**Audit mode:**
1. Clear Chrome → launch directly to gemini.google.com → dismiss FRE + mic permission + app banner
2. Type audit prompt → Send → wait for generation
3. CDP `scrollIntoView` positions response at top → ADB screencap

### ChatGPT (chatgpt.com)

**Appium mode (seeding):**
1. [NATIVE] Clear Chrome → dismiss FRE → navigate
2. [WEBVIEW] Find `#prompt-textarea` → type via React-aware JS setter → click `#composer-submit-button`
3. Wait → scroll → follow-up

**ADB mode (seeding):**
1. Clear Chrome → FRE → navigate
2. Find `prompt-textarea` via `uiautomator dump` → type → find `composer-submit-button` → tap
3. Wait → scroll → follow-up

**Audit mode:**
1. Clear Chrome → launch to chatgpt.com → dismiss FRE
2. Type audit prompt → Send → wait for generation
3. CDP `scrollIntoView` → ADB screencap

### Perplexity (www.perplexity.ai)

**Appium mode (seeding):**
1. [NATIVE] Clear Chrome → FRE → navigate → dismiss Comet modals (native)
2. [WEBVIEW] Dismiss remaining modals via JS → find `#ask-input` → type via `execCommand` → JS click Submit
3. Wait → scroll → follow-up

**ADB mode (seeding):**
1. Clear Chrome → FRE → navigate
2. Dismiss Comet modals by tapping X at confirmed coordinates `(w*0.943, h*0.134)`
3. Find `ask-input` via `uiautomator dump` → type
4. Hide keyboard (tap content area) → poll until Submit button at `y > 1400` → tap
5. Wait → scroll → follow-up

**Audit mode:**
1. Clear Chrome → launch to perplexity.ai → dismiss FRE + Comet modals
2. Type audit prompt → keyboard hide + submit poll → wait for generation
3. CDP `scrollIntoView` on `.prose` / `ol` element → ADB screencap

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

### Seeding Flow
1. OpenClaw calls `GET {RUNNER_HOST}/clients` → gets client data + remaining keywords
2. Generates prompts for each remaining keyword
3. Sends `POST {RUNNER_HOST}/run-all` with all sessions
4. Server distributes, runs in parallel, logs results

### Audit Flow
1. OpenClaw (or scheduler) calls `POST {RUNNER_HOST}/audit` with optional filters
2. Server discovers devices, runs audit in background on all devices
3. Screenshots + text saved to `audit_results/`
4. Check results: `GET {RUNNER_HOST}/audit/status`

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
| Audit screenshot | CDP `scrollIntoView` + ADB `screencap` (single image) |
| Audit CDP | `adb forward` + WebSocket to Chrome DevTools, unique port per device |
| Audit text extract | CDP `Runtime.evaluate` with per-platform JS selectors |
| Audit parallel | Unique CDP ports (9222 + device index) prevent `adb forward` collision |
| Audit logging | `audit_log.json` — timestamp, client, keyword, platform, status, paths |

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

### Audit CDP connection failed
Each device needs a unique CDP port in parallel mode. The `--all-devices` flag handles this automatically (9222 + device index). If running manually, pass different `--cdp-port` values.

### Audit screenshot shows home screen
The screenshot was taken after Chrome closed. Make sure `take_screenshot` runs before `driver.quit()` (Appium mode) or while Chrome is still open (ADB mode).

### Gemini microphone permission popup (Samsung)
Handled automatically by `_dismiss_gemini_popups()` — taps "Never allow" / "Block". If still appearing, check the device screen and tap manually.

### Gemini response in table format
The audit prompt includes "Use a numbered list format, not a table" to prevent this. If Gemini still uses tables, the prompt may need further tweaking for that specific keyword.

---

## Quick Reference

```bash
# ── Daily Startup ──
bash start_appium.sh                    # Terminal 1: Appium servers
python3 setup_devices.py --assign       # Assign devices (auto-detects mode)
python3 server.py                       # Terminal 2: API server
cloudflared tunnel --url http://localhost:5001  # Terminal 3: tunnel

# ── Seeding Tests ──
python3 test_adb_flows.py --platform Gemini     # ADB test (Infinix)
python3 test_flows.py --platform Gemini          # Appium test (others)
python3 main.py --test --clients 3               # multi-device test
python3 main.py --dry-run --clients 3            # preview plan

# ── Ranking Audit ──
python3 audit.py --test --platform Gemini                    # single device
python3 audit.py --test --all-devices --exclude Redmi        # all devices parallel
python3 audit.py --all-devices --exclude Redmi               # production audit
curl -X POST localhost:5001/audit -H "Content-Type: application/json" -d '{"exclude":"Redmi"}'
curl localhost:5001/audit/status                             # check results

# ── Operations ──
python3 main.py --status                         # today's seeding status
python3 main.py --reset                          # reset rotation
curl http://localhost:5001/health                # API health
curl http://localhost:5001/clients               # client status
curl http://localhost:5001/audit/status          # audit results

# ── Adding Devices ──
bash start_appium.sh
python3 setup_devices.py --assign
# restart server.py

# ── Debug ──
adb -s <serial> shell settings put system pointer_location 1
```
