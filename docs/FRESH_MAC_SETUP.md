# Fresh Mac Setup — AEO Executor

Step-by-step for bringing up the AEO executor on a brand-new Mac. End state:
a working Mac that can run daily AEO sessions (225 jobs/day, 10 Android phones,
rolling=5 concurrent).

---

## What runs on this Mac

```
┌────────────────────────────────────────────────────────────────────┐
│  Mac (Mac mini / MacBook / PC)                                     │
│                                                                    │
│   run_daily_all.py                                                 │
│   ├── fetch_admin_state()  ← GET /api/clients, /api/keywords, ...  │
│   ├── build_jobs()         ← randomize + plan 225 sessions         │
│   ├── hydrate_prompts()    ← DeepSeek pre-generates all prompts    │
│   └── run_rolling()        ← dispatch 5 concurrent sessions        │
│         │                                                          │
│         ├── gost (per-session)  → gate.decodo.com:10001            │
│         │                        Decodo residential proxy          │
│         │                                                          │
│         ├── Device Manager (docker)  localhost:8080                │
│         │   └── SocksDroid on phone ← VPN tunnel to proxy          │
│         │                                                          │
│         └── ADB (WiFi)  → 10 Android phones                        │
│                           Chrome + platform flows                  │
└────────────────────────────────────────────────────────────────────┘
```

---

## 1. Prerequisites

```bash
# Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Android Platform Tools (adb)
brew install --cask android-platform-tools

# gost (SOCKS5 multiplexer)
brew install gost

# Docker Desktop (for Device Manager)
brew install --cask docker

# Python 3.14+
brew install python@3.14

# Python packages
pip3 install requests websocket-client

# Optional — scrcpy for viewing phone screens
brew install scrcpy

# Optional — cloudflared if admin/scheduler is remote
brew install cloudflared
```

Set ANDROID_HOME:
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

```bash
cat > .env <<'EOF'
PROXY_HOST=gate.decodo.com
PROXY_PORT=10001
PROXY_BASE_USER=user-spmqebjuzf
PROXY_PASSWORD=<get-from-current-mac-or-1password>
DEVICE_MANAGER_URL=http://localhost:8080
DEEPSEEK_API_KEY=<get-from-current-mac>
EOF
chmod 600 .env
```

Copy all values from the current Mac's `.env`.

---

## 4. Start Device Manager (Docker)

```bash
cd ~/projects/device-manager
git clone <device-manager-repo> ~/projects/device-manager
docker compose up -d

# Verify
curl -sf http://localhost:8080/device/list && echo "Device Manager: UP"
```

---

## 5. Connect Android phones

### 5.1 One-time per-phone setup

1. Developer Options → **USB Debugging** ON
2. Developer Options → **Wireless debugging** ON  
3. Developer Options → **Disable Permission Monitoring** ON (OPPO/Realme)
4. Install **SocksDroid** APK on each phone:
   ```bash
   adb install ~/projects/device-manager/src/main/resources/apk/socksdroid/base.apk
   ```
5. Connect via USB first time to accept RSA key fingerprint
6. Enable ADB over WiFi:
   ```bash
   adb -s <usb-serial> tcpip 5555
   adb connect <phone-ip>:5555
   ```
7. Battery optimization: Settings → Battery → App Power Saving → allow SocksDroid + VPN

### 5.2 Daily connection

Phones must be on the same WiFi as the Mac. Each day:
```bash
# Connect all pool IPs
for ip in 116 119 109 136 101 105 106 111 107 110; do
  adb connect 192.168.0.$ip:5555
done
adb devices | grep ":5555" | wc -l   # should be 10
```

### 5.3 Assign devices to the pool

Edit `active_devices.json` manually:
```json
{
  "device-101": {
    "serial": "192.168.0.116:5555",
    "port": 4731,
    "use_adb": true,
    "brand": "vivo",
    "model": "V2430"
  },
  ...
}
```

Each device gets a unique port (4731–4740). All use `"use_adb": true` (no Appium).

---

## 6. Smoke tests

```bash
cd ~/projects/aeo-appium

# 1. Device connectivity
adb devices | grep ":5555" | wc -l   # should be 10

# 2. gost + Decodo proxy
python3 gost_manager.py test           # should print PASS

# 3. Admin API
curl -s -o /dev/null -w "Admin: %{http_code}" \
  -H "X-Executor-Token: <token>" \
  https://jjm59vpn3y.us-east-1.awsapprunner.com/api/businesses
# should print 200

# 4. Device Manager
curl -sf http://localhost:8080/device/list && echo "UP"
```

---

## 7. Run a daily session

```bash
cd ~/projects/aeo-appium

# Step A — Generate plan (fetches admin data + pre-generates prompts via DeepSeek)
python3 -u run_daily_all.py --generate-only daily_plan_$(date +%F).json

# Step B — Launch (rolling=5, 5 phones busy at once, 10 phones in pool)
nohup python3 -u run_daily_all.py \
  --load=daily_plan_$(date +%F).json \
  --batch-size=25 --rolling=5 --no-pause \
  > daily_run_$(date +%F)_main.log 2>&1 &

# Step C — Monitor
grep "PASS\|FAIL" daily_run_$(date +%F)_main.log
```

Typical: 225 sessions, ~75-84% pass rate on first run, 1-3 remainder retries for failures.

---

## 8. Remainder runs (retry failures)

```bash
# Build remainder plan from failed triples
python3 << 'PYEOF'
import json, glob, csv
plan = json.load(open('daily_plan_2026-04-28.json'))
jobs = [j for w in plan['waves'] for j in w]
files = sorted(glob.glob('sessions_log.daily_20260428_*.csv'))
successes = set()
for f in files:
    for r in csv.DictReader(open(f)):
        if r.get('status') == 'success':
            successes.add((str(r.get('campaign_id','')).strip(), (r.get('keyword','') or '').strip(), (r.get('platform','') or '').strip().lower()))
remaining = []
for j in jobs:
    cid = str(j.get('campaign_id','')).strip(); kw = (j.get('keyword_text','') or '').strip(); plat = (j.get('platform','') or '').strip().lower()
    if (cid, kw, plat) not in successes:
        found = False
        for s_cid, s_kw, s_plat in successes:
            if s_cid == cid and s_plat == plat and (kw.lower() in s_kw.lower() or s_kw.lower() in kw.lower()):
                found = True; break
        if not found: remaining.append(j)
waves = [[j] for j in remaining]
json.dump({'waves': waves}, open('daily_plan_REMAINDER.json', 'w'), indent=2)
print(f'Remaining: {len(remaining)}')
PYEOF

# Launch remainder (rolling=3 for smaller batches)
nohup python3 -u run_daily_all.py \
  --load=daily_plan_REMAINDER.json \
  --batch-size=25 --rolling=3 --no-pause \
  > daily_run_REMAINDER.log 2>&1 &
```

Repeat until 190+ unique passes.

---

## 9. Post-run: consolidate CSV

```bash
# After all passes, build final CSV (passes + fails, 32 columns)
python3 << 'PYEOF'
import glob, csv
files = sorted(glob.glob('sessions_log.daily_20260428_*.csv'))
all_rows = []
for f in files:
    all_rows.extend(list(csv.DictReader(open(f))))
seen = {}
for r in sorted(all_rows, key=lambda x: x.get('timestamp', '')):
    key = (str(r.get('campaign_id','')).strip(), (r.get('keyword','') or '').strip(), (r.get('platform','') or '').strip().lower())
    if key not in seen: seen[key] = r
    elif r.get('status') == 'success' and seen[key].get('status') != 'success': seen[key] = r
deduped = sorted(seen.values(), key=lambda x: x.get('timestamp', ''))
passes = sum(1 for r in deduped if r.get('status') == 'success')
cols = list(csv.DictReader(open(files[0])).fieldnames)
with open('sessions_log.daily_2026-04-28.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
    w.writeheader(); w.writerows(deduped)
print(f'{len(deduped)} rows ({passes} pass + {len(deduped)-passes} fail)')
PYEOF

# Split into pass_01–04 CSVs (50/50/50/≤40)
python3 << 'PYEOF'
import csv
rows = list(csv.DictReader(open('sessions_log.daily_2026-04-28.csv')))
pass_only = [r for r in rows if r.get('status') == 'success']
cols = list(rows[0].keys())
for start, end, n in [(0,50,'01'), (50,100,'02'), (100,150,'03'), (150,190,'04')]:
    chunk = pass_only[start:end]
    if not chunk: break
    with open(f'sessions_log.daily.pass_{n}_2026-04-28.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader(); w.writerows(chunk)
    print(f'pass_{n}: {len(chunk)} rows')
PYEOF
```

---

## 10. Push to admin

The consolidated CSV and pass_01–04 CSVs are sent to the admin developer for push
via `push_sessions_to_admin.py`:

```bash
# Push pass CSVs separately (deduped within each pass)
python3 push_sessions_to_admin.py sessions_log.daily.pass_01_2026-04-28.csv
python3 push_sessions_to_admin.py sessions_log.daily.pass_02_2026-04-28.csv
python3 push_sessions_to_admin.py sessions_log.daily.pass_03_2026-04-28.csv
python3 push_sessions_to_admin.py sessions_log.daily.pass_04_2026-04-28.csv
```

The admin endpoint is at `https://jjm59vpn3y.us-east-1.awsapprunner.com`.

---

## 11. CSV column schema (32 columns)

As defined in `run_daily_all.py` → `SESSION_CSV_COLUMNS`:

```
timestamp, date, wave_index, client_id, client_name, biz_name,
search_address, campaign_id, campaign_name, keyword, prompt, follow_up,
has_follow_up, device_id, platform, status, duration_s, proxy_status,
proxy_username, proxy_host, proxy_port, base_latitude, base_longitude,
mocked_latitude, mocked_longitude, mocked_timezone, backlinks_expected,
backlink_injected, backlink_found, backlink_url, failure_step, error
```

NEVER invent columns — always copy from the runner CSV's `reader.fieldnames`.

---

## 12. Common failure modes

| Symptom | Fix |
|---|---|
| `Device Manager unreachable` | Docker not running — `docker compose up -d` in device-manager dir |
| `gost exited early with code 1` | Internet down or Decodo gateway unreachable — check WiFi |
| `generation_timeout` | Content policy stall or slow AI — retry on different device/platform |
| `PROXY_PASSWORD is not set` | `.env` missing — fix §3 |
| `gost binary not found` | `brew install gost` |
| Devices offline after DHCP lease | Re-check WiFi IPs on phones, update `active_devices.json` |
| ADB over WiFi not enabled | `adb -s <usb-serial> tcpip 5555` via USB first |
| SocksDroid VPN flickers off (Infinix) | Battery optimization kills it — allow in Settings → Battery |

---

## 13. File / port cheat sheet

| What | Where | Port |
|---|---|---|
| Daily runner | `python3 -u run_daily_all.py --load=...` | — |
| Plan generator | `python3 -u run_daily_all.py --generate-only ...` | — |
| Device Manager | Docker in `~/projects/device-manager` | 8080 |
| gost (per-session) | Spun up by runner | 11001+ |
| Pool config | `~/projects/aeo-appium/active_devices.json` | — |
| Proxy creds | `~/projects/aeo-appium/.env` | — |
| Admin API | `jjm59vpn3y.us-east-1.awsapprunner.com` | 443 |
| Decodo proxy | `gate.decodo.com` | 10001 |

---

## 14. Related docs

- `docs/EXECUTOR_PAYLOAD.md` — Job / JobResult contract for the scheduler
- `docs/DAILY_SESSION_RUNBOOK.md` — full runbook with pre-run checklist
- `docs/MANUAL_RUN.md` — legacy manual run guide (gost details)
