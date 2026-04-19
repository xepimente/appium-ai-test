# AEO Executor — Manual Run Guide

End-to-end runbook for running AEO sessions by hand, from proxy setup through
admin push and teardown. This is the **human operator** counterpart to
`docs/EXECUTOR_PAYLOAD.md` (which documents the scheduler→executor contract).

All paths are relative to `/Users/seolocalph/projects/aeo-appium`.

---

## 0. When to use which path

| You want to… | Run this |
|---|---|
| Run 5 sessions for one admin client, push results to admin | `python3 run_client_daily.py <clientId> --push` |
| Re-run a client from scratch (delete today's data + counters) | `python3 cleanup_client.py <clientId>` then the above |
| Chain multiple clients overnight with auto-teardown | `bash run_overnight.sh` |
| Push an existing CSV that wasn't auto-pushed | `python3 push_sessions_to_admin.py <csv_path>` |
| Weekly ranking audit (screenshots per platform) | `python3 audit.py --all-devices` |
| Smoke-test one device with hardcoded prompt | `python3 test_single_device.py` |
| Verify gost + Decodo wiring without running a session | `python3 gost_manager.py test` |

For the sequential vs parallel decision, see §5. For the scheduler payload
contract (when transport is wired in), see `docs/EXECUTOR_PAYLOAD.md`.

---

## 1. Prerequisites — one-time setup

### 1.1 Binaries
```bash
brew install gost                      # SOCKS5 multiplexer used by gost_manager.py
brew install --cask android-platform-tools
```

### 1.2 `.env`
```
PROXY_HOST=gate.decodo.com
PROXY_PORT=10001
PROXY_BASE_USER=user-spknlt0736
PROXY_PASSWORD=<decodo-password>
```
Both `run_client_daily.py` and `gost_manager.py` read this at import time.

### 1.3 Device Manager (Java service)
The device-manager Spring Boot service on `localhost:8080` sends
`am start -n net.typeblog.socks/.AdbStartActivity` intents to SocksDroid on
each phone. If it's down, VPN connect fails silently.

```bash
cd ~/app/device-manager && java -jar ./lib/device-manager-0.0.1-SNAPSHOT.jar
curl -sf http://localhost:8080/device/list   # should return JSON
```

### 1.4 Phone-side requirements
- USB debugging + USB debugging (Security Settings) both ON
- **SocksDroid** app installed and paired with device-manager
- Screen on / awake before run: `adb -s <serial> shell input keyevent KEYCODE_WAKEUP`

---

## 2. Connect devices

```bash
adb devices
bash start_appium.sh                   # only needed for Appium-mode devices (non-Infinix)
python3 setup_devices.py --assign      # writes active_devices.json
```

`active_devices.json` maps `device-101..device-105` to `{serial, port, use_adb}`.
Infinix/TECNO phones get `use_adb: true` — they skip Appium entirely.

**If `adb devices` shows a `(2)` suffix** on a wireless serial (e.g. after
reconnect), that's normal — the ADB helpers tab-split and handle it. Just make
sure `active_devices.json` reflects the current serial string.

---

## 3. The proxy layer (gost + Decodo)

### 3.1 Architecture
```
Phone SocksDroid ──LAN──▶ Mac gost listener ──WAN──▶ Decodo residential exit
(192.168.0.102:11001)      (one process, N listeners)    (sticky session per listener)
```

- Phones **no longer** connect to `gate.decodo.com` directly.
- Each gost listener (`11001..11005` by default) chains to its own Decodo
  sticky session — different session id + zip per phone.
- `GostManager` writes a YAML config at runtime because gost v3's CLI pools
  all `-F` flags into one shared hop pool (which breaks per-listener pinning).

### 3.2 Smoke-test before a real run
```bash
python3 gost_manager.py test
```
Expected: `[self-test] PASS` — one listener on `127.0.0.1:11001` resolves to a
Decodo exit in Florida (zip `32504` hardcoded in the self-test).

### 3.3 In a real session
`session_runner.run_parallel()` wraps the session list in a `with GostManager([...])`
block and injects `proxy_config["gost"] = gm.mapping[device_id]` per session.
`proxy.connect_proxy` reads that and points SocksDroid at the LAN endpoint with
`anon/anon` creds instead of the WAN Decodo creds.

Sequential runs (`run_sequential`) currently skip gost — each device takes the
direct Decodo tunnel, which is fine for 1-at-a-time.

---

## 4. Session prompt templates

See `run_client_daily.py:48` (daily) and `audit.py:44` (audit). The daily
prompt is deterministic string-format; the `agents/prompt_generator.py`
DeepSeek-based generator is **not** wired into the current daily flow.

---

## 5. Running a daily session for one client

### 5.1 The command
```bash
python3 run_client_daily.py <clientId> [--push] [--parallel]
```

| Flag | Effect |
|---|---|
| (none) | Run 5 sessions sequentially, write `sessions_log.<slug>.csv`. No admin push. |
| `--push` | After CSV write, POST each row to `/api/sessions` + PATCH keyword counters. |
| `--parallel` | Use `run_parallel` (gost multiplexer). Default is sequential. |

### 5.2 What it does, step by step
1. Fetches client / businesses / AEO plans / active keywords from admin
2. Randomly picks `SESSIONS_PER_CLIENT` (5) keywords
3. For each: picks a random platform (ChatGPT/Gemini/Perplexity), builds prompt
   + follow-up, assembles a `proxy_cfg` with the business's zip + lat/lng
4. Calls `run_parallel` or `run_sequential`
5. Writes `sessions_log.<slug>.csv` with full proxy metadata + backlink-click
   results
6. If `--push`: POSTs sessions + bumps keyword counters (see §7)

### 5.3 Timing
- **Sequential** (default): ~4–6 min per session × 5 = **~20–30 min**
- **Parallel via gost**: **~3–5 min total** for 5 sessions

Sequential is the default because it's easier to observe and debug; parallel
is proven reliable once gost is involved. Flip the default in
`run_client_daily.py:294` if you want parallel-by-default.

---

## 6. Re-running a client (cleanup first)

If a run pushed bad data (fabricated, or counter-bumped without real sessions),
reverse it before re-running:

```bash
python3 cleanup_client.py <clientId> --dry    # preview
python3 cleanup_client.py <clientId>          # actually DELETEs sessions + decrements counters
python3 run_client_daily.py <clientId> --push
```

`cleanup_client.py` hits the admin DELETE endpoints deployed 2026-04-18:
- `DELETE /api/sessions/:id`
- `PATCH /api/keywords/:id` with decremented `initialSearchCount*` / `followupSearchCount*`

---

## 7. Pushing an existing CSV

If a run wrote a CSV but you skipped `--push`, or a push failed midway:
```bash
python3 push_sessions_to_admin.py sessions_log.<slug>.csv
```
Resolves admin IDs by `(clientId, businessId, keywordText)` triple, POSTs
sessions, groups by keyword, PATCHes counters. Writes a summary to
`audit_logs/session_push_<timestamp>.json`.

---

## 8. Overnight chain (multiple clients)

`run_overnight.sh` is a hardcoded chain — edit the client IDs inline:

```bash
for cid in 5 2 3; do
    python3 -u run_client_daily.py $cid --push > "run_<name>.log" 2>&1
done
```

When all clients finish, it:
1. Iterates `active_devices.json` and calls `proxy.teardown_device(serial)` on each
2. `pkill`s any lingering `run_client_daily.py` / `session_runner` processes
3. Writes `run_overnight_done.log` as the completion marker

It **waits on `pgrep -f "run_client_daily.py 4"`** at the top — that's how it
hooks onto a manually-started earlier run. If you're not stacking onto a
running Mark client, delete that wait-loop or the chain will wait forever.

---

## 9. Verification — what "success" actually means

Every session runs through `verify_submit_succeeded()` in `flows_adb.py`. A
session is only marked `success` when all five hold:

1. The Submit/Send tap fired on a real button
2. `wait_for_generation` saw a Stop button OR response-complete marker
3. After generation: the input box **no longer contains the prompt**
4. No platform-error banner is visible in the UI dump
5. If there was a follow-up: same 4 checks for the follow-up

If any check fails, `status: error` + a specific error string (see
`docs/EXECUTOR_PAYLOAD.md` §3 for the error-code table).

---

## 10. Teardown — leaving the lab clean

After a manual run, disconnect Decodo on every device and kill strays:

```bash
python3 - <<'PY'
import json
from proxy import teardown_device
with open("active_devices.json") as f:
    pool = json.load(f)
for did, info in pool.items():
    try:
        r = teardown_device(info["serial"])
        print(f"{did}: {r.get('status','?')}")
    except Exception as e:
        print(f"{did}: {e}")
PY

pkill -f "run_client_daily.py" 2>/dev/null || true
pkill -f "session_runner"       2>/dev/null || true
pkill -f "gost -C"              2>/dev/null || true     # in case GostManager crashed
```

`run_overnight.sh` already does this at the end; it's only needed manually if
you ran `run_client_daily.py` by hand and want to drop the VPN tunnels.

---

## 11. Troubleshooting

| Symptom | Check | Fix |
|---|---|---|
| `PROXY_PASSWORD is not set` | `grep PROXY_PASSWORD .env` | Add it. GostManager won't start without it. |
| `gost exited early with code N` | Run `gost_manager.py test` in isolation | Usually a stale gost process on ports 11001-11005 — `pkill -f "gost -C"` |
| `gost listener on port X not accepting` | Something else is on that port | `lsof -iTCP:11001 -sTCP:LISTEN` — kill it or bump `base_port` |
| Sessions return `submit_failed` | UI dump shows prompt still in input box | Re-read the session's error + screenshot — usually an A11y timing issue |
| Perplexity-only submit misses | Chrome WebView A11y is stale ~3s after typing | `flows_adb.run_perplexity` has a `time.sleep(5)` after `type_text` — do not add this to ChatGPT/Gemini |
| "Are they a sGot" corruption | IME autocomplete injection during word-by-word typing | `type_text` should be single-shot with `%s` spaces — confirm you didn't regress it |
| Admin PATCH fails | Usually wrong `X-Executor-Token` or stale `/api/keywords/:id` | Re-fetch current counts and retry |
| Daily push counters double-count | You ran `--push` twice | `cleanup_client.py` → re-run |
| Device drops WAN during parallel run | Too many direct Decodo tunnels | Use gost (parallel mode already does this) |

---

## 12. Reference — scripts touched by this flow

| Script | Responsibility |
|---|---|
| `gost_manager.py` | Mac-side SOCKS5 multiplexer; writes YAML at runtime |
| `proxy.py` | `connect_proxy` (reads `gost` override), `teardown_device` |
| `session_runner.py` | `run_parallel` / `run_sequential` / `run_session` |
| `flows_adb.py` | Per-platform ADB automation + `verify_submit_succeeded` |
| `run_client_daily.py` | Per-client orchestration + optional `--push` |
| `cleanup_client.py` | DELETE sessions + reverse counters |
| `push_sessions_to_admin.py` | Push an existing CSV |
| `run_overnight.sh` | Chain multiple clients with teardown |
| `active_devices.json` | Device pool (serial, port, use_adb flag) |

For the Job/JobResult payload contract (scheduler transport), see
`docs/EXECUTOR_PAYLOAD.md`.

---

## 13. Running as a service for the scheduler (HTTP transport)

When the scheduler needs to POST jobs instead of anyone running scripts by
hand, start the FastAPI executor:

```bash
python3 -m aeo_executor
```

It binds `0.0.0.0:8100` by default (override with `AEO_EXECUTOR_HOST` /
`AEO_EXECUTOR_PORT`). Endpoints:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/jobs` | Run one Job synchronously, return JobResult |
| `GET` | `/health` | Liveness + device pool size + busy flag |
| `GET` | `/status` | Current in-flight job, if any |

The Job/JobResult schema is authoritative in `docs/EXECUTOR_PAYLOAD.md`.

### Minimal scheduler call

```bash
curl -sS -X POST http://localhost:8100/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "job_id": "sched-test-001",
    "client_id": 4,
    "business_id": 22,
    "keyword_id": 15,
    "keyword_text": "local marketing agency",
    "platform": "Perplexity",
    "prompt": "I am looking for recommendations on local marketing agency in Pensacola, FL. A friend mentioned TestCo. Are they solid?",
    "device_id": "device-102"
  }'
```

Returns the full JobResult with every input field echoed plus `status`,
`error`, `started_at`, `finished_at`, `duration_s`, `proxy_resolved`,
`response_preview`, `backlink_clicked`, and `steps`.

### Full scheduler call (with proxy + follow-up + backlinks)

```bash
curl -sS -X POST http://localhost:8100/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "job_id": "daily-c4-001",
    "client_id": 4,
    "business_id": 22,
    "keyword_id": 15,
    "keyword_text": "local marketing agency",
    "platform": "Perplexity",
    "prompt": "I am looking for recommendations on local marketing agency in Pensacola, FL. A friend mentioned TestCo.",
    "follow_up": "Got any specific examples of their recent work?",
    "backlinks": ["medium.com", "clutch.co"],
    "proxy": {
      "country": "us",
      "zip": "32504",
      "session_duration": 30,
      "latitude": 30.4213,
      "longitude": -87.2169,
      "timezone": "America/Chicago"
    },
    "device_id": "device-102"
  }'
```

### Concurrency

The executor runs **N Jobs in parallel — one per device**. The scheduler can
POST as many Jobs as there are free devices in `active_devices.json`.

Error codes the scheduler should handle:

| Status | Meaning | Scheduler action |
|---|---|---|
| `200` | Job completed — inspect `status` / `error` in the JobResult body | proceed |
| `400 device_id ... not in pool` | Unknown `device_id` | remove stale entry from scheduler's device list |
| `400 device_unreachable` | `device_serial` present but ADB cannot reach it | mark serial offline in scheduler's fleet DB |
| `409 device_busy: …` | Target device running another Job | retry with `device_id: null` or a different identifier |
| `422` | Both `device_id` and `device_serial` set | pick one — they are mutually exclusive |
| `503 all N devices in use` / `active_devices.json is empty` | Pool saturated or empty, and no `device_serial` was passed | backoff + retry |

### Device selection — two modes

The scheduler picks **one** of these per Job (or neither, for auto-pick):

| Field | Behavior | `active_devices.json` needed? |
|---|---|---|
| `device_id: "device-102"` | Look up in pool; use pool's serial/port/use_adb | ✅ |
| `device_serial: "adb-…"` | Use serial directly; auto-detect `use_adb` via `getprop ro.product.brand` | ❌ |
| neither | Auto-pick first free pool entry | ✅ |
| both | Rejected with 422 Unprocessable Entity | — |

Recommendation for schedulers that already track phones by serial: use
`device_serial` and skip the pool file entirely.

### Proxy behavior

When `Job.proxy` is set, the executor:
1. Allocates a free local port (11001-12000) for this Job's gost listener
2. Spins up a single-device `GostManager` chained to a fresh Decodo sticky
   session with the Job's zip / country / session_duration
3. Points SocksDroid on the phone at the Mac's LAN IP + allocated port
4. Runs the session; tears gost down on finish
5. Echoes the gost endpoint in `proxy_resolved.gost_endpoint`

When `Job.proxy` is null, the session runs on whatever network the phone is
on (clearnet). `proxy_resolved` is null in the JobResult.

### Scaling the pool

`active_devices.json` is re-read on every Job. To add/remove phones:
```bash
python3 setup_devices.py --assign   # rewrites active_devices.json
```
The executor picks up the change on the next incoming Job — no restart.

