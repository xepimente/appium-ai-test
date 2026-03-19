# AEO DroidRun Orchestrator

Automates Answer Engine Optimization (AEO) sessions on physical Android devices. Each device runs human-like queries on Gemini, ChatGPT, and Perplexity to improve client ranking in AI-powered search engines.

---

## How It Works

1. **Prompt generation** — DeepSeek LLM generates a natural, human-like seeding prompt per client × keyword, with an optional casual follow-up (50% chance).
2. **Session distribution** — All pending keyword sessions are distributed round-robin across all 13 devices. Each device runs its assigned sessions sequentially; all devices run in parallel.
3. **On-device execution** — The DroidRun agent opens Chrome, searches Google for the AI platform (Gemini / ChatGPT / Perplexity), navigates to it, types the prompt, reads the full response by scrolling, and optionally sends a follow-up.
4. **Logging** — Results are written to `sessions_log.json` and rotation state to `device_rotation.json`.

---

## Project Structure

```
driodrun/
├── main.py                   # Orchestrator — entry point for all commands
├── setup_devices.py          # Install Portal APK on all devices
├── check_devices.py          # Check USB debugging (Security Settings) status
├── check_devices.sh          # Same check as shell script
├── launch_scrcpy.sh          # Open scrcpy windows for all connected devices
├── clients.json              # Client list with keywords
├── device_assignments.json   # Device → client assignments
├── device_rotation.json      # Tracks sessions run today (auto-generated)
├── sessions_log.json         # Full session history log (auto-generated)
├── requirements.txt          # Python dependencies
├── config/
│   └── config.yaml           # DroidRun agent config (max_steps, LLM profiles)
└── agents/
    ├── session_runner.py     # Runs DroidAgent per device; parallel execution
    ├── prompt_generator.py   # Generates seeding prompts via DeepSeek
    └── ranking_auditor.py    # Generates weekly ranking audit prompts
```

---

## Requirements

- Python 3.10+
- [adb](https://developer.android.com/tools/adb) installed and on `PATH`
- [scrcpy](https://github.com/Genymobile/scrcpy) installed (for screen mirroring)
- DeepSeek API key
- 13 Android devices connected via wireless ADB (WiFi)

---

## Initial Setup

### 1. Clone and create virtual environment

```bash
git clone <repo-url>
cd driodrun
python3 -m venv venv
source venv/bin/activate       # macOS/Linux
# venv\Scripts\activate        # Windows
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt` installs:
- `droidrun[deepseek]` — Android automation + DeepSeek LLM support
- `llama-index-llms-openai-like` — LLM chat interface

### 3. Set your DeepSeek API key

```bash
export DEEPSEEK_API_KEY="your-key-here"
```

To make it permanent, add it to your shell profile (`~/.zshrc` or `~/.bashrc`):

```bash
echo 'export DEEPSEEK_API_KEY="your-key-here"' >> ~/.zshrc
source ~/.zshrc
```

---

## Device Setup

### Step 1 — Connect devices via wireless ADB

On each Android device:
1. Go to **Settings → Developer Options**
2. Enable **USB Debugging**
3. Enable **Wireless Debugging**
4. Tap **Pair device with pairing code** and run:

```bash
adb pair <device-ip>:<port>
# Enter the pairing code shown on the device
adb connect <device-ip>:5555
```

Verify all devices are visible:

```bash
adb devices
```

### Step 2 — Enable USB Debugging (Security Settings)

This is a **separate** toggle from regular USB debugging. It allows `adb shell input` injection (required for DroidRun to tap and type on the screen).

Check which devices need it:

```bash
python check_devices.py
```

Or using the shell script:

```bash
bash check_devices.sh
```

Output:
- `✅ OK` — device is ready
- `❌ NEEDS FIX` — needs the security toggle enabled

**How to fix on each device brand:**

| Brand | Path |
|-------|------|
| Xiaomi / MIUI | Settings → Additional Settings → Developer Options → **USB debugging (Security Settings)** |
| OPPO / Realme | Settings → Additional Settings → Developer Options → **Disable permission monitoring** |
| Infinix / TECNO | Settings → System → Developer Options → **USB debugging (Security Settings)** |
| Samsung | Settings → Developer Options → toggle USB debugging off → on → Reboot |
| Vivo / iQOO | Settings → Developer Options → **USB debugging (Security Settings)** |

After enabling, reboot the device and re-run `python check_devices.py` to confirm.

### Step 3 — Assign healthy devices

Run a full health check (online + USB injection + Portal) and write only the passing devices to `active_devices.json`. `main.py` will use this file automatically on every run.

```bash
python setup_devices.py --assign
```

Output per device:
- `✅ all checks passed → assigned` — included in `active_devices.json`
- `❌ SKIPPED: <reason>` — excluded (fix the issue, then re-run `--assign`)

Re-run after fixing any device to update the assignment:

```bash
python setup_devices.py --assign
```

To go back to using all devices regardless of status, delete `active_devices.json`:

```bash
rm active_devices.json
```

### Step 4 — Install DroidRun Portal APK

DroidRun requires its Portal accessibility service APK installed on every device.

Check status and auto-install on all devices:

```bash
python setup_devices.py
```

Check status only (no install):

```bash
python setup_devices.py --check
```

Output per device:
- `✅ Portal OK + accessibility enabled` — ready to run
- `🔧 Installing...` — auto-installing now
- `⚠️ Install attempted but failed` — needs manual action (see below)

**If auto-install fails** (common on Xiaomi/Redmi):

1. On the device: **Settings → Additional Settings → Developer Options → Install from unknown sources** → enable for ADB
2. Re-run: `python setup_devices.py`
3. If the device shows an install dialog on-screen, tap **Install**
4. After install, go to **Settings → Accessibility → DroidRun Portal** and enable it

### Step 4 — View all devices on screen (optional)

Open a tiled scrcpy window for all connected devices (4-column grid):

```bash
bash launch_scrcpy.sh
```

Each window is 320×568px. Press `Ctrl+C` to close all windows.

---

## Adding / Editing Clients

Edit `clients.json`. Each client object:

```json
{
  "id": 0,
  "biz_name": "Business Name",
  "plan": "Monthly-Pro",
  "city": "City",
  "state": "State",
  "address": "Full address",
  "biz_url": "https://example.com",
  "gmb_url": "https://maps.app.goo.gl/...",
  "keywords": [
    "keyword one city",
    "keyword two city",
    "keyword three"
  ]
}
```

- `id` must be unique and sequential
- `gmb_url` is optional — if present, prompts may reference the Google Maps profile
- Each client can have any number of keywords; all will be distributed across devices

After adding clients, update `device_assignments.json` to include the new client IDs for each device. The file maps device IDs to arrays of client IDs they are assigned to.

---

## Adding Devices

Edit the `DEVICE_POOL` dict in `main.py` and `setup_devices.py`:

```python
DEVICE_POOL = {
    "device-014": {"serial": "YOUR_DEVICE_SERIAL"},
}
```

Find a device's serial by running `adb devices`. For wireless connections, the short serial is the part before the `-<hash>` suffix in the full transport name.

---

## Daily Operations

### Run daily AEO sessions

```bash
python main.py
```

This will:
1. Load all clients and pending keywords (not yet run today)
2. Distribute sessions round-robin across all 13 devices
3. Generate a DeepSeek prompt + optional follow-up for each session
4. Run all device queues in parallel (each device's sessions run sequentially)
5. Log results to `sessions_log.json`

### Check today's status

```bash
python main.py --status
```

Shows which devices have run, which keywords are done, and how many remain per client.

### Reset today's rotation (for testing / re-running)

```bash
python main.py --reset
```

Clears today's rotation so all keywords are treated as pending again. Use this during testing or if you need to re-run a day's sessions.

### Run weekly ranking audit

```bash
python main.py --audit
```

Runs a ranking audit session for every client × keyword. The audit prompt asks each AI platform to name the top 3 businesses for that keyword and whether the client ranks among them. Results are logged to `sessions_log.json`.

---

## Recommended Daily Workflow

```bash
# 1. Activate virtual environment
source venv/bin/activate

# 2. Verify all devices are connected
adb devices

# 3. Install Portal on any device that needs it
python setup_devices.py

# 4. Assign only healthy devices (online + USB inject + Portal OK)
python setup_devices.py --assign

# 5. (Optional) View devices on screen
bash launch_scrcpy.sh &

# 6. Run today's sessions (uses active_devices.json automatically)
python main.py

# 6. Check results
python main.py --status
```

---

## Configuration

### `config/config.yaml`

| Setting | Default | Description |
|---------|---------|-------------|
| `agent.max_steps` | 80 | Max steps the DroidRun agent can take per session |
| `agent.vision` | false | Enable screenshot-based vision (slower) |
| `agent.reasoning` | false | Enable chain-of-thought reasoning |
| `agent.wait_for_stable_ui` | 0.3s | Wait time after each UI action |
| `agent.after_sleep_action` | 0.5s | Sleep after tap/swipe |
| `device.platform` | android | Target platform |
| `llm_profiles.*` | DeepSeek deepseek-chat | LLM used by the DroidRun agent |

The session timeout is set to **600 seconds (10 minutes)** per session in `agents/session_runner.py`.

### Session platforms

Each session is assigned a random platform from: `Gemini`, `ChatGPT`, `Perplexity`.

To change the platform distribution, edit `PLATFORMS` in `main.py`:

```python
PLATFORMS = ["Gemini", "ChatGPT", "Perplexity"]
```

---

## Logs and Data Files

| File | Description |
|------|-------------|
| `sessions_log.json` | Full history of all sessions with status, prompt, platform, device |
| `device_rotation.json` | Today's rotation state — which keywords have run on which device |
| `config/app_cards/` | DroidRun app card configs for faster app recognition |
| `trajectories/` | Step-by-step GIFs of each session (saved by DroidRun) |

---

## Troubleshooting

### `device 'XXXXXXX' not found`

The device is connected over wireless ADB but its serial isn't resolving. Run:

```bash
adb devices
```

Make sure the device appears with status `device` (not `offline` or `unauthorized`). The orchestrator automatically resolves wireless mDNS transport names at runtime — if a device is offline, it will be skipped with a warning.

### `INJECT_EVENTS` error

The device needs **USB debugging (Security Settings)** enabled. Run `python check_devices.py` to identify which devices need it, then follow the brand-specific steps in the Device Setup section above.

### Portal APK install fails

- **Xiaomi/Redmi**: Enable "Install from unknown sources" or "Disable permission monitoring" in Developer Options, then re-run `python setup_devices.py`
- **Any device**: If an install dialog appears on the physical screen, tap **Install**
- After install, manually enable accessibility: **Settings → Accessibility → DroidRun Portal → Enable**

### Sessions timing out or hitting step limit

Increase `max_steps` in `config/config.yaml` (currently 80). Also check that the device has a stable WiFi connection and Chrome is not showing a CAPTCHA — clearing Chrome data before each session (done automatically) helps avoid this.

### `sessions_log.json` stays empty

Make sure you are not looking at the wrong file. The log is at the **project root**: `sessions_log.json`, not inside a `logs/` subdirectory.

