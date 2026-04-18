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

  // ── Prompt content (required) ──────────────────────────────────────────────
  "prompt":        "I'm looking for ...",      // string — initial user message
  "follow_up":     "Any examples?",            // string|null — optional follow-up

  // ── Backlinks (optional) ───────────────────────────────────────────────────
  // Strings that should be matched as substrings against any URL cited in the
  // AI's response. First match wins and is clicked + briefly dwelled on.
  "backlinks": ["medium.com", "clutch.co"],

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

  // ── Device selection (optional) ────────────────────────────────────────────
  // Pin to a specific device, or leave out to let the executor pick the next
  // free one from its active_devices.json pool.
  "device_id":     "device-101"
}
```

### Required vs optional

| Field            | Required | Default if omitted               |
|------------------|:--------:|----------------------------------|
| job_id           |    ✓     |                                  |
| client_id        |    ✓     |                                  |
| business_id      |    ✓     |                                  |
| keyword_id       |    ✓     |                                  |
| keyword_text     |    ✓     |                                  |
| platform         |    ✓     |                                  |
| prompt           |    ✓     |                                  |
| follow_up        |          | `null`                           |
| backlinks        |          | `[]`                             |
| proxy            |          | `null` — runs on clearnet        |
| device_id        |          | auto-picked from device pool     |

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

## 5. Transport (TBD)

The current executor is invoked as a Python function:
```python
from session_runner import run_session
result = run_session(serial, full_serial, port, platform, prompt, follow_up,
                     device_id, sess_meta={...proxy/backlinks/...})
```

Future transports (to be decided by the scheduler):
- **HTTP:** `POST /v1/jobs` with Job body → returns JobResult synchronously, or
  202 + `GET /v1/jobs/{job_id}` to poll
- **Queue:** SQS/Redis consumer pulls Job from `jobs-pending`, pushes JobResult
  to `jobs-done`
- **Webhook:** scheduler registers a URL, executor POSTs JobResult when done

---

## 6. Examples

### 6.1 Minimum viable Job (no proxy, no follow-up, no backlinks)
```json
{
  "job_id":       "test-001",
  "client_id":    4,
  "business_id":  22,
  "keyword_id":   15,
  "keyword_text": "local marketing agency",
  "platform":     "ChatGPT",
  "prompt":       "Recommend a local marketing agency in Pensacola, FL."
}
```

### 6.2 Full Job with proxy + backlinks + follow-up
```json
{
  "job_id":       "daily-c4-001",
  "client_id":    4,
  "business_id":  22,
  "keyword_id":   15,
  "keyword_text": "local marketing agency",
  "platform":     "Perplexity",
  "prompt":       "I'm looking for recommendations on local marketing agency in Pensacola, FL. A friend mentioned TestCo.",
  "follow_up":    "Got any specific examples of their recent work?",
  "backlinks":    ["medium.com", "clutch.co"],
  "proxy": {
    "country":          "us",
    "zip":              "32504",
    "session_duration": 30,
    "latitude":         30.4213,
    "longitude":        -87.2169,
    "timezone":         "America/Chicago"
  },
  "device_id": "device-101"
}
```

### 6.3 Successful JobResult
```json
{
  "job_id":       "daily-c4-001",
  "client_id":    4,
  "business_id":  22,
  "keyword_id":   15,
  "keyword_text": "local marketing agency",
  "platform":     "Perplexity",
  "prompt":       "I'm looking...",
  "follow_up":    "Got any specific...",
  "backlinks":    ["medium.com", "clutch.co"],
  "proxy":        { "...": "..." },
  "device_id":    "device-101",

  "status":       "success",
  "error":        null,
  "started_at":   "2026-04-18T22:15:04Z",
  "finished_at":  "2026-04-18T22:18:32Z",
  "duration_s":   208.2,
  "device_serial":"adb-149145555W006589-...",
  "proxy_resolved": {
    "status":    "CONNECTED",
    "exit_ip":   "68.228.26.18",
    "exit_city": "Pensacola",
    "exit_zip":  "32501"
  },
  "response_preview": "Based on reviews from Clutch.co and Yelp, TestCo is a solid choice in Pensacola...",
  "backlink_clicked": "https://clutch.co/agencies/pensacola?utm_source=perplexity",
  "steps": ["dismissed_fre", "navigated", "typed_prompt", "sent_prompt", "generation_complete", "scrolled", "typed_followup", "sent_followup", "scrolled_followup", "backlink_clicked:https://clutch.co/..."]
}
```

### 6.4 Failed JobResult (submit missed)
```json
{
  "job_id":       "daily-c4-002",
  "client_id":    4,
  "platform":     "Perplexity",
  "...":          "all input fields echoed",

  "status":       "error",
  "error":        "submit_failed: input still contains 'Im looking for recommendations on lo...'",
  "started_at":   "2026-04-18T22:20:00Z",
  "finished_at":  "2026-04-18T22:22:30Z",
  "duration_s":   150.0,
  "device_id":    "device-102",
  "backlink_clicked": null,
  "response_preview": "",
  "steps": ["dismissed_fre", "navigated", "typed_prompt", "sent_prompt", "generation_timeout", "verify_failed:submit_failed: ..."]
}
```
