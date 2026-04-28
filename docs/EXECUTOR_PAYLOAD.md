# AEO Executor — Job Payload Contract

This doc is the **contract between the scheduler and the on-device executor**.
The scheduler owns job definition. The executor owns running the job on a
physical Android device and returning a structured result.

- **Scheduler → Executor:** sends a `Job` (input schema)
- **Executor → Scheduler:** returns a `JobResult` (same payload + status)

The executor does **not** push anything to admin directly. The scheduler takes
the `JobResult` and decides whether to push, retry, alert, or discard.

---

## 1. Job (input payload)

```jsonc
{
  // ── Job identity (required) ────────────────────────────────────────────────
  "job_id":        "j_2026_04_18_0001",        // string — scheduler's unique id
  "client_id":     4,                          // int    — admin clientId
  "business_id":   22,                         // int    — admin businessId
  "keyword_id":    15,                         // int    — admin keywordId
  "keyword_text":  "local marketing agency",   // string — for logging/prompt
  "platform":      "ChatGPT",                  // enum   — "ChatGPT" | "Gemini" | "Perplexity"
  "type":          "daily",                    // enum   — "daily" (seeding) | "audit" (ranking)

  // ── Audit fields (only when type = "audit") ────────────────────────────────
  // When type = "audit", the executor auto-builds a ranking prompt using these
  // fields and extracts [RANK: X/Y] from the AI response. The prompt, follow_up,
  // and backlinks fields are ignored for audit jobs.
  "biz_name":      "American Plumbing Co",     // string — only for audit
  "biz_url":       "https://americanplumbing-co.com",  // string — only for audit
  "city":          "Pensacola",                // string — only for audit
  "state":         "FL",                       // string — only for audit

  // ── Prompt content (required for daily) ────────────────────────────────────
  // For type = "audit", these are ignored — the audit prompt is auto-generated.
  "prompt":        "I'm looking for ...",      // string — initial user message
  "follow_up":     "Any examples?",            // string|null — optional follow-up

  // ── Backlinks (optional) ───────────────────────────────────────────────────
  // Matched against URLs cited in the AI's response sources panel. First match
  // wins and is clicked + briefly dwelled on.
  //
  // For article backlinks, the url is the target. For GBP (Google Business
  // Profile) backlinks, the maps.app.goo.gl short URL is never cited by AI —
  // use embedded_url (the actual website inside the GBP) for matching instead.
  "backlinks": [
    {"url": "https://medium.com/@.../article",   "type": "article", "embedded_url": ""},
    {"url": "https://maps.app.goo.gl/RLYdz9XmB", "type": "gbp",     "embedded_url": "https://americanplumbing-co.com"}
  ],

  // ── Geolocation / proxy (optional) ─────────────────────────────────────────
  // If omitted, session runs on the Mac's clearnet IP with no GPS mock.
  // If present, the executor connects SocksDroid via Mac-side gost to a
  // residential Decodo session in the target zip, plus mocks GPS ±5mi and
  // sets Android timezone.
  "proxy": {
    "country":          "us",
    "zip":              "32504",
    "session_duration": 30,                    // Decodo sticky-session minutes
    "latitude":         30.4213,
    "longitude":        -87.2169,
    "timezone":         "America/Chicago"
  },

  // ── Device selection (pass ONE or neither — they are mutually exclusive) ──
  // Option A — logical pool id (requires active_devices.json on executor):
  "device_id":     "device-101"

  // Option B — raw ADB serial (skips pool lookup, works standalone):
  // "device_serial": "adb-149145555W005208-27c1FH (2)._adb-tls-connect._tcp"

  // Option C — omit both: executor auto-picks first free entry from pool.
}
```

### Required vs optional

| Field          | Required | Default if omitted |
|----------------|:--------:|--------------------|
| job_id         |    ✓     |                    |
| client_id      |    ✓     |                    |
| business_id    |    ✓     |                    |
| keyword_id     |    ✓     |                    |
| keyword_text   |    ✓     |                    |
| platform       |    ✓     |                    |
| type           |          | `"daily"`          |
| prompt         | daily    |                    |
| follow_up      |          | `null`             |
| biz_name       | audit    |                    |
| biz_url        | audit    |                    |
| city           | audit    |                    |
| state          | audit    |                    |
| backlinks      |          | `[]`               |
| proxy          |          | `null` — runs on clearnet |
| device_id      |          | see device-selection table below |
| device_serial  |          | see device-selection table below |

### Device selection — which field to pass

| Scheduler sends                  | Executor behavior                                                                  | `active_devices.json` needed? |
|----------------------------------|------------------------------------------------------------------------------------|:-----------------------------:|
| `device_id: "device-102"`        | Look up in pool; use pool's serial/port/use_adb                                    | ✅                            |
| `device_serial: "adb-…"`         | Use serial directly; auto-detect use_adb via `getprop ro.product.brand`            | ❌                            |
| neither set                      | Auto-pick first free pool entry                                                    | ✅                            |
| both set                         | `422 Unprocessable Entity` — mutually exclusive                                    | —                             |

Recommendation for schedulers managing their own device fleet: **pass
`device_serial`** and ignore the pool file entirely.

---

## 2. JobResult (output payload)

The executor echoes the full Job back with these additional fields populated:

```jsonc
{
  // ── All Job fields from the input are echoed back verbatim ────────────────
  "job_id":        "j_2026_04_18_0001",
  "client_id":     4,
  ...

  // ── Execution result ───────────────────────────────────────────────────────
  "status":         "success",                 // "success" | "error"
  "error":          null,                      // null on success; see error codes below

  // ── Timing ─────────────────────────────────────────────────────────────────
  "started_at":     "2026-04-18T22:15:04Z",    // ISO-8601 UTC
  "finished_at":    "2026-04-18T22:18:32Z",
  "duration_s":     208.2,                     // seconds

  // ── Device that ran it ─────────────────────────────────────────────────────
  "device_id":      "device-101",
  "device_serial":  "adb-149145555W006589-2W7yzb (2)._adb-tls-connect._tcp",

  // ── Resolved proxy details (null if proxy was null in the Job) ────────────
  "proxy_resolved": {
    "status":            "CONNECTED",          // "CONNECTED" | "ERROR" | "SKIPPED"
    "gost_endpoint":     "192.168.0.102:11001",
    "upstream_session":  "user-spknlt0736-session-abc123-...",
    "exit_ip":           "68.228.26.18",       // what public IP the phone saw
    "exit_city":         "Pensacola",
    "exit_region":       "Florida",
    "exit_zip":          "32501",
    "mocked_latitude":   30.4042,              // after ±5mi randomization
    "mocked_longitude":  -87.1982,
    "mocked_timezone":   "America/Chicago"
  },

  // ── Audit result (only when type = "audit") ────────────────────────────────
  // Keyed by platform. Present if the audit prompt was run and ranking was found.
  "audit": {
    "ChatGPT": {
      "position":  7,
      "total":     "25",
      "mentioned": true,
      "context":   "[RANK: 7/25] American Plumbing Co is a solid mid-tier choice..."
    }
  },

  // ── Evidence / observations ────────────────────────────────────────────────
  "response_preview":   "Based on reviews...", // first 200 chars of AI response
  "backlink_clicked":   "https://clutch.co/...", // URL clicked, or null if none

  // ── Detailed trace (for debugging / retry decisions) ──────────────────────
  "steps": [
    "dismissed_fre",
    "navigated",
    "typed_prompt",
    "sent_prompt",
    "generation_complete",
    "scrolled",
    "typed_followup",
    "sent_followup",
    "scrolled_followup",
    "backlink_clicked:https://clutch.co/..."
  ]
}
```

---

## 3. Error codes

When `status == "error"`, the `error` field contains a short code + colon +
human-readable detail. The scheduler can decide retry/alert behavior per code.

| Code                    | When                                                     | Retry recommended? |
|-------------------------|----------------------------------------------------------|:------------------:|
| `submit_failed`         | Prompt still in the input box after tap (submit missed)  | Yes — new session  |
| `generation_timeout`    | No AI response after 180 s                               | Yes                |
| `followup_timeout`      | No follow-up response after 180 s                        | Yes                |
| `followup_submit_failed`| Follow-up submit tap missed                              | Yes                |
| `platform_error: X`     | Page showed a platform-side error banner                 | Yes (rate limit)   |
| `fre_stuck`             | Chrome First-Run dialogs couldn't be dismissed           | Yes                |
| `proxy_error`           | gost/SocksDroid didn't establish a tunnel                | Yes w/ backoff     |
| `device_offline`        | ADB lost the device mid-session                          | Pin different dev  |

Example error payload:
```json
{
  "status": "error",
  "error":  "submit_failed: input still contains 'Im looking for recommen...'",
  "steps":  ["dismissed_fre", "navigated", "typed_prompt", "verify_failed:submit_failed: ..."]
}
```

---

## 4. Verification semantics (what `status: success` actually means)

A JobResult with `status: success` passes **all** of:

1. `sent_prompt` step fired (we tapped a real Submit/Send button)
2. `wait_for_generation` saw a Stop button at least once OR detected response-complete markers
3. **After generation finished, the input box no longer contained the prompt text** (`verify_submit_succeeded` passed)
4. No known platform-error banner was visible in the UI dump
5. If `follow_up` was set: same 4 checks above pass for the follow-up

If any check fails, `status: error` with the specific reason is returned. Prior
to today, the executor could return a false-positive `success` when the submit
tap missed but `wait_for_generation` timed out and the flow proceeded. The
verification layer closes that gap.

---

## 5. Transport

**Current:** HTTP sync. Scheduler POSTs a Job, blocks until the session
finishes, reads the JobResult from the response body.

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/v1/jobs` | Run one Job synchronously. 200 = completed (check `status` in body); 4xx/5xx = pre-execution error |
| `GET`  | `/health` | Liveness + pool size + in-flight count |
| `GET`  | `/status` | List of currently in-flight jobs |

**Base URL:** `http://<executor-host>:8100` (default port; override with
`AEO_EXECUTOR_PORT`).

Typical session duration 2–4 minutes, so the scheduler's HTTP client timeout
should be ≥ 600 s.

Future transport options under consideration (not yet implemented): async
202 + poll, SQS/Redis queue consumer, webhook callbacks.

---

## 6. Examples

All examples assume the executor is reachable at `http://192.168.0.102:8100`.

### 6.1 Minimum viable Job (no proxy, no follow-up, no backlinks)

```bash
curl -sS -X POST http://192.168.0.102:8100/v1/jobs \
  -H 'Content-Type: application/json' \
  --max-time 600 \
  -d '{
    "job_id":       "smoke-001",
    "client_id":    4,
    "business_id":  22,
    "keyword_id":   15,
    "keyword_text": "local marketing agency",
    "platform":     "ChatGPT",
    "prompt":       "Recommend a local marketing agency in Pensacola, FL."
  }'
```

Use case: smoke test, or when geo-targeting doesn't matter.

### 6.2 Full Job with proxy + follow-up + backlinks, pinned by `device_id`

```bash
curl -sS -X POST http://192.168.0.102:8100/v1/jobs \
  -H 'Content-Type: application/json' \
  --max-time 600 \
  -d '{
    "job_id":       "daily-c4-2026-04-19-001",
    "client_id":    4,
    "business_id":  22,
    "keyword_id":   15,
    "keyword_text": "local marketing agency",
    "platform":     "Perplexity",
    "prompt":       "I am looking for recommendations on local marketing agency in the Pensacola, FL area. A friend mentioned TestCo. Are they a solid choice, or are there stronger options locally? If you can, cite the sources or links you are using.",
    "follow_up":    "Got any specific examples of their recent work or reviews I can check?",
    "backlinks":    [{"url": "https://medium.com/@.../article", "type": "article", "embedded_url": ""}, {"url": "https://clutch.co/...", "type": "article", "embedded_url": ""}, {"url": "https://maps.app.goo.gl/...", "type": "gbp", "embedded_url": "https://testco.com"}],
    "proxy": {
      "country":          "us",
      "zip":              "32504",
      "session_duration": 30,
      "latitude":         30.4213,
      "longitude":        -87.2169,
      "timezone":         "America/Chicago"
    },
    "device_id": "device-102"
  }'
```

Use case: production daily session — geo-pinned to client's business location.
Requires `active_devices.json` on the executor host.

### 6.3 Same Job — using `device_serial` (no pool file needed)

```bash
curl -sS -X POST http://192.168.0.102:8100/v1/jobs \
  -H 'Content-Type: application/json' \
  --max-time 600 \
  -d '{
    "job_id":       "daily-c4-2026-04-19-002",
    "client_id":    4,
    "business_id":  22,
    "keyword_id":   15,
    "keyword_text": "local marketing agency",
    "platform":     "Perplexity",
    "prompt":       "...",
    "follow_up":    "...",
    "backlinks":    [{"url": "https://clutch.co/agencies/pensacola", "type": "article", "embedded_url": ""}],
    "proxy": {
      "country": "us", "zip": "32504", "session_duration": 30,
      "latitude": 30.4213, "longitude": -87.2169,
      "timezone": "America/Chicago"
    },
    "device_serial": "adb-149145555W005208-27c1FH (2)._adb-tls-connect._tcp"
  }'
```

Use case: scheduler manages its own phone fleet and tracks raw ADB serials.
Executor does not consult `active_devices.json`; auto-detects `use_adb` via
`getprop ro.product.brand` on first access.

### 6.4 Audit Job (ranking extraction)

```bash
curl -sS -X POST http://192.168.0.102:8100/v1/jobs \
  -H 'Content-Type: application/json' \
  --max-time 600 \
  -d '{
    "job_id":        "audit-c4-2026-04-28-001",
    "client_id":     4,
    "business_id":   22,
    "keyword_id":    15,
    "keyword_text":  "drain cleaning",
    "type":          "audit",
    "biz_name":      "American Plumbing Co",
    "biz_url":       "https://americanplumbing-co.com",
    "city":          "San Diego",
    "state":         "CA",
    "proxy": {
      "country": "us", "zip": "92102", "session_duration": 30,
      "latitude": 32.7157, "longitude": -117.1611,
      "timezone": "America/Los_Angeles"
    },
    "device_id": "device-101"
  }'
```

The executor auto-builds the ranking prompt from `keyword_text`, `biz_name`, `biz_url`,
`city`, `state`. For audit jobs, the `platform` field is ignored — the executor runs
**all 3 platforms** (ChatGPT, Gemini, Perplexity) sequentially under the same proxy.
Returns audit result with ranking position per platform.

### 6.5 Auto-pick (scheduler doesn't pin a device)

```bash
curl -sS -X POST http://192.168.0.102:8100/v1/jobs \
  -H 'Content-Type: application/json' \
  --max-time 600 \
  -d '{
    "job_id":       "auto-001",
    "client_id":    5,
    "business_id":  30,
    "keyword_id":   42,
    "keyword_text": "pediatric clinic",
    "platform":     "Gemini",
    "prompt":       "I am looking for a pediatric clinic in Miami, FL. A friend mentioned Leo Lapuerta MD. Are they a good choice?",
    "follow_up":    "Any recent reviews I should check?",
    "proxy": {
      "country":          "us",
      "zip":              "33101",
      "session_duration": 30,
      "latitude":         25.7617,
      "longitude":        -80.1918,
      "timezone":         "America/New_York"
    }
  }'
```

Omit both `device_id` and `device_serial` to let the executor pick the first
free pool entry. Returns `503 no_free_devices` if all pool entries are busy.

### 6.5 Successful JobResult (response body)

```jsonc
{
  // — all Job fields echoed back verbatim —
  "job_id":       "daily-c4-2026-04-19-001",
  "client_id":    4,
  "business_id":  22,
  "keyword_id":   15,
  "keyword_text": "local marketing agency",
  "platform":     "Perplexity",
  "prompt":       "I am looking for recommendations on local marketing agency in the Pensacola, FL area. A friend mentioned TestCo...",
  "follow_up":    "Got any specific examples of their recent work or reviews I can check?",
  "backlinks":    [{"url": "https://medium.com/@.../article", "type": "article", "embedded_url": ""}, {"url": "https://clutch.co/...", "type": "article", "embedded_url": ""}, {"url": "https://maps.app.goo.gl/...", "type": "gbp", "embedded_url": "https://testco.com"}],
  "proxy": {
    "country":          "us",
    "zip":              "32504",
    "session_duration": 30,
    "latitude":         30.4213,
    "longitude":        -87.2169,
    "timezone":         "America/Chicago"
  },
  "device_id":      "device-102",

  // — execution fields —
  "status":         "success",
  "error":          null,
  "started_at":     "2026-04-19T04:32:15Z",
  "finished_at":    "2026-04-19T04:35:28Z",
  "duration_s":     193.4,

  "device_serial":  "adb-149145555W005208-27c1FH (2)._adb-tls-connect._tcp",

  "proxy_resolved": {
    "status":           "CONNECTED",
    "gost_endpoint":    "192.168.0.102:11001",
    "upstream_session": "user-spknlt0736-session-ab3kz7-sessionduration-30-country-us-zip-32504",
    "exit_ip":          "68.228.26.18",
    "exit_city":        "Pensacola",
    "exit_region":      "Florida",
    "exit_zip":         "32501",
    "mocked_latitude":  30.4042,
    "mocked_longitude": -87.1982,
    "mocked_timezone":  "America/Chicago"
  },

  "response_preview": "Based on reviews from Clutch.co and local directories, TestCo appears to be a solid mid-tier marketing agency in Pensacola...",
  "backlink_clicked": "https://clutch.co/agencies/pensacola",
  "steps": [
    "dismissed_fre",
    "navigated",
    "found_input",
    "typed_prompt",
    "sent_prompt",
    "generation_complete",
    "scrolled",
    "typed_followup",
    "sent_followup",
    "scrolled_followup",
    "backlink_clicked:https://clutch.co/agencies/pensacola"
  ]
}
```

### 6.6 Failed JobResult — submit missed

```jsonc
{
  "job_id":       "daily-c4-2026-04-19-003",
  "client_id":    4,
  "business_id":  22,
  "keyword_id":   15,
  "keyword_text": "local marketing agency",
  "platform":     "Perplexity",
  "prompt":       "I am looking...",
  "follow_up":    "Got any specific...",
  "backlinks":    [{"url": "https://clutch.co/agencies/pensacola", "type": "article", "embedded_url": ""}],
  "proxy":        { "country": "us", "zip": "32504", ... },
  "device_id":    "device-102",

  "status":       "error",
  "error":        "submit_failed: input still contains 'I am looking for recommendati...'",
  "started_at":   "2026-04-19T04:40:00Z",
  "finished_at":  "2026-04-19T04:42:30Z",
  "duration_s":   150.0,

  "device_serial":  "adb-149145555W005208-27c1FH (2)._adb-tls-connect._tcp",
  "proxy_resolved": { "status": "CONNECTED", "gost_endpoint": "192.168.0.102:11001", ... },

  "response_preview": "",
  "backlink_clicked": null,
  "steps": [
    "dismissed_fre",
    "navigated",
    "found_input",
    "typed_prompt",
    "sent_prompt",
    "generation_timeout",
    "verify_failed:submit_failed: input still contains 'I am looking for recommendati...'"
  ]
}
```

Scheduler action: retry with a new `job_id` on any free device — not a code
bug, just session-level flakiness.

### 6.7 HTTP-level error examples

All return a JSON body with a `detail` field:

```bash
# 409 — target device is running another job
curl -sS -o /dev/null -w "%{http_code}\n" -X POST .../v1/jobs -d '{
  "job_id":"busy-test", ...same client/biz/keyword..., "device_id":"device-102"
}'
# 409
# {"detail":"device_busy: device-102 (in use by daily-c4-2026-04-19-001)"}

# 400 — unreachable serial
curl -sS -X POST .../v1/jobs -d '{
  "job_id":"unreachable-test", ..., "device_serial":"adb-DOES-NOT-EXIST-xxx"
}'
# 400
# {"detail":"device_unreachable: adb cannot reach 'adb-DOES-NOT-EXIST-xxx'"}

# 422 — both identifiers passed
curl -sS -X POST .../v1/jobs -d '{
  "job_id":"both-test", ..., "device_id":"device-102", "device_serial":"adb-..."
}'
# 422
# {"detail":[{"type":"value_error","msg":"...mutually exclusive..."}]}

# 503 — no free devices and auto-pick pool is empty / saturated
curl -sS -X POST .../v1/jobs -d '{"job_id":"sat-test", ...}'
# 503
# {"detail":"all 5 devices currently in use"}
```

### 6.8 Python scheduler snippet

```python
import requests

EXECUTOR_URL = "http://192.168.0.102:8100"

job = {
    "job_id":       f"sched-{int(time.time())}",
    "client_id":    4,
    "business_id":  22,
    "keyword_id":   15,
    "keyword_text": "local marketing agency",
    "platform":     "Perplexity",
    "prompt":       "...",
    "follow_up":    "...",
    "backlinks":    [{"url": "https://clutch.co/agencies/pensacola", "type": "article", "embedded_url": ""}],
    "proxy": {
        "country": "us", "zip": "32504", "session_duration": 30,
        "latitude": 30.4213, "longitude": -87.2169,
        "timezone": "America/Chicago",
    },
    "device_serial": "adb-149145555W005208-27c1FH (2)._adb-tls-connect._tcp",
}

resp = requests.post(f"{EXECUTOR_URL}/v1/jobs", json=job, timeout=600)

if resp.status_code == 200:
    result = resp.json()
    if result["status"] == "success":
        print(f"✓ {result['job_id']} — {result['duration_s']}s on {result['device_serial']}")
        preview = (result.get('response_preview') or '')[:100]
        print(f"  response: {preview}...")
        print(f"  backlink clicked: {result.get('backlink_clicked')}")
    else:
        print(f"✗ {result['job_id']} failed: {result['error']}")
        # Decide retry based on the short code before the colon:
        code = (result.get('error') or '').split(':', 1)[0]
        if code in {'submit_failed', 'generation_timeout', 'followup_timeout'}:
            # retry-safe — flake, not a code bug
            ...
elif resp.status_code == 400:
    print(f"bad device reference: {resp.json()['detail']}")
elif resp.status_code == 409:
    print(f"device busy — retry elsewhere: {resp.json()['detail']}")
elif resp.status_code == 422:
    print(f"invalid payload: {resp.json()['detail']}")
elif resp.status_code == 503:
    print(f"pool saturated — backoff + retry: {resp.json()['detail']}")
else:
    print(f"unexpected: {resp.status_code} {resp.text}")
```
