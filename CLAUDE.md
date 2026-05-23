# aeo-appium

Drives real Android phones via ADB + Appium + scrcpy to run AEO sessions and ranking audits. Talks to AEOAdmin (separate repo) over HTTP for prompts, variants, and result storage.

## Commands

```bash
# Install
pip install -r requirements.txt

# Daily AEO sessions (default flow)
python main.py                        # all clients, parallel across active_devices.json
python run_client_daily.py <client>   # single client end-to-end
python run_daily_all.py               # rolling dispatcher across all clients

# Ranking audit
python run_audit.py                   # parallel audit, gost+proxy, paste input

# HTTP executor (FastAPI)
python -m aeo_executor                # POST /jobs to run sessions remotely

# Flask API
python server.py                      # /run-aeo, /run-all, /status, /health

# Status / debug
python main.py --status               # today's rotation
python main.py --reset                # reset today's rotation (testing only)
adb devices                           # connected phones
```

## Key context

- AEOAdmin owns the database and LLM service. This repo never talks to Postgres directly — it calls `https://<admin>/api/llm/*` and `/api/sessions`.
- Daily rule: 5 sessions per client per day, sampled across platforms (Gemini / ChatGPT / Perplexity).
- gost runs as a per-job SOCKS5 multiplexer on the Mac so each device hits Decodo with its own zip-coded proxy.

## clients_audit_targets.json — audit catalog

`device-agent`'s `audit_dispatch_http.py:_find_catalog_entry(keyword_id)` reads this file to resolve per-business audit config: `biz_url`, `proxy.zip`, `city`, `state`. Without an entry, the dispatcher falls back to NY zip `10001` — which makes Gemini / ChatGPT / Perplexity reject the audit on geo-mismatch.

**MUST BE IDENTICAL ON EVERY MAC** that consumes from the shared RabbitMQ queue, otherwise audits for the same `keyword_id` will use different proxy zips depending on which Mac picks the job up.

Entry shape (per business):

```json
{
  "client_name": "...",
  "client_id": 4,
  "biz_name": "...",
  "biz_id": 7,
  "biz_url": "https://maps.app.goo.gl/...",
  "city": "Pensacola",
  "state": "FL",
  "biz_address": "5328 N Davis Hwy",
  "keywords": [
    {"keyword": "eye exam", "keyword_id": 41},
    {"keyword": "optometrist", "keyword_id": 35}
  ],
  "proxy": {"session_duration": 30, "country": "us", "zip": "32504"}
}
```

### Adding a new business

1. Pull biz details from `https://<admin>/api/businesses/<id>` and its keywords from `/api/keywords?businessId=<id>`.
2. Pick `proxy.zip`: prefer the actual address's zip (parsed from `publishedAddress`). If Decodo doesn't support it, the dispatcher's `_resolve_zip` maps to the state's known-good (see `audit_dispatch_http.py:_STATE_GOOD_ZIP`).
3. If `biz_url` is empty (no GMB record), **synthesize a Google Maps search URL** — the audit prompt requires *some* URL or geo-validation fails:
   ```
   https://www.google.com/maps/search/<urlencoded biz_name + biz_address>
   ```
   This pattern is in use for biz_id 20, 23, 33 (added 2026-05-24 after the 21-job no-url failure on the May 21 backfill).
4. Append the entry to `clients_audit_targets.json`, commit, push.

### Refreshing from admin

The file is a snapshot of admin data. No auto-refresh script exists yet — entries must be added manually when admin adds clients/businesses/keywords. A `*.json.bak` sibling lives in the repo as the last known full snapshot.
