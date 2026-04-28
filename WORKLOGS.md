# AEO Automation — Worklogs

---

## Ticket 1

**Title:** AEO Automation — Screenshot Capture R&D, CDP Integration & Audit Prompt Engineering

**Description:**
Research and evaluate screenshot capture approaches for the AI ranking audit system. The goal is to capture a clean screenshot of only the AI's ranking response on Android devices running Chrome. This ticket covers evaluating 4 approaches, building the CDP integration for pixel-perfect viewport positioning, engineering the audit prompt, and testing across Appium and ADB-only devices.

**Acceptance Criteria:**
- 4 screenshot approaches evaluated with pros/cons
- CDP scrollIntoView positions AI response at top of viewport
- Audit prompt produces concise ranked list fitting in 1 mobile screen
- screenshot.py module with CDP scroll + ADB screencap
- Gemini audit flow working on both ADB and Appium modes
- Samsung and Infinix devices tested and producing clean screenshots

### Worklog — Mar 24, 2026 (Tuesday) — 8 hours

**Title:** AEO Automation — Screenshot Capture R&D, CDP Integration & Audit Prompt Engineering

- Researched and evaluated 4 screenshot approaches for capturing full AI responses on mobile:
  - ADB `screencap` (single screen only — too limited for long responses)
  - Scroll + stitch with Pillow (worked but had duplicated headers/content overlap)
  - Chrome DevTools Protocol full-page capture (`Page.captureScreenshot` with `captureBeyondViewport`)
  - Native Android scroll screenshot (`keyevent 120` + "Capture more" — not automatable)
- Tested scroll + stitch approach — built `test_screenshot_stitch.py` with pixel-level overlap detection via row hashing
  - Identified overlap deduplication issue: Gemini header and input bar repeated between frames
- Tested CDP approach — built `test_screenshot_cdp.py` connecting via `adb forward` + WebSocket
  - Hit `403 Forbidden` origin error — fixed with `suppress_origin=True` on WebSocket connection
  - CDP produced clean single-image full-page capture but captures entire page (including user prompt)
- Decided on hybrid approach: **CDP for scroll positioning + ADB screencap for capture**
  - CDP `scrollIntoView({block: 'start'})` positions the AI response at the top of viewport
  - Single `adb exec-out screencap -p` captures exactly what's on screen
  - No stitching, no full-page — just the response visible on one screen
- Engineered the audit ranking prompt to produce concise responses fitting in 1 mobile screen:
  - Initial prompt too verbose — Gemini gave long paragraphs with nested bullet points
  - Added "no intro, no bullet points, no extra detail" and "under 100 words" constraints
  - Added "not a table" instruction — Gemini was rendering tables that got cut off on mobile
  - Final prompt: top 3 businesses, single-line format per business, one-sentence leader assessment
- Built `screenshot.py` module:
  - `take_screenshot()` — simple ADB screencap
  - `scroll_response_to_top()` — CDP JS scrollIntoView with -150px offset for sticky headers
  - `extract_response_text()` — CDP JS to grab response element's innerText
  - Per-platform CSS selectors: `model-response` (Gemini), `[data-message-author-role="assistant"]` (ChatGPT), `.prose` (Perplexity)
  - CDP connection helper with `adb forward` + WebSocket + auto-disconnect
  - ADB fallback scroll when CDP unavailable
- Built `test_audit_gemini.py` — end-to-end test for both ADB and Appium modes:
  - ADB mode: clear Chrome → launch directly to Gemini → FRE → type + send → wait → CDP scroll → screencap
  - Appium mode: clear Chrome → Appium session (app_package/app_activity, not browser_name) → FRE → WebView → type + send → wait → JS scroll → screencap before driver.quit()
- Fixed Appium "chrome not reachable" error — `pm clear` kills Chrome, need `app_package`/`app_activity` caps instead of `browser_name`
- Fixed Gemini "Chat with Gemini in an app" banner not dismissing:
  - JS dismiss failed (banner is native overlay, not web element)
  - Native Appium tap failed (content-desc "Close" not found)
  - Final fix: ADB coordinate tap at `(w*0.09, h*0.23)` — same approach as Perplexity Comet modal
- Tested on Samsung SM-A075F (Appium) and Infinix X6725 (ADB) — both producing clean screenshots

---

## Ticket 2

**Title:** AEO Automation — Multi-Platform Audit Flows, Parallel Device Execution & API Integration

**Description:**
Build the production AI ranking audit system with all 3 platform flows, multi-device parallel execution with round-robin keyword distribution, and Flask API endpoints for remote triggering. The system distributes all client keywords across available devices, runs each device's queue sequentially while all devices execute in parallel, and saves screenshots + response text with structured naming.

**Acceptance Criteria:**
- Gemini, ChatGPT, Perplexity audit flows working via ADB
- Round-robin keyword distribution: 1 device + 1 client = 1 keyword + 1 random platform
- Multi-device parallel execution with unique CDP ports
- POST /audit and GET /audit/status Flask API endpoints
- 5 devices tested passing in parallel audit run
- README updated with audit system documentation

### Worklog — Mar 25, 2026 (Wednesday) — 8 hours

**Title:** AEO Automation — Multi-Platform Audit Flows, Parallel Device Execution & API Integration

- Built `audit.py` — production audit module with all 3 platform flows:
  - `audit_gemini_adb()` — navigate to gemini.google.com, dismiss FRE + mic permission + app banner, type prompt, wait, CDP scroll, screencap
  - `audit_chatgpt_adb()` — navigate to chatgpt.com, longer load wait (8s), find prompt-textarea, send, wait, CDP scroll, screencap
  - `audit_perplexity_adb()` — navigate to perplexity.ai, dismiss Comet modal, type, keyboard hide + submit poll, wait, CDP scroll, screencap
- Implemented structured file naming: `{client_id}_{keyword-slug}_{timestamp}.png`
  - Platform-separated directories: `audit_results/Gemini/`, `audit_results/ChatGPT/`, `audit_results/Perplexity/`
  - Text responses in `audit_results/text/` with platform suffix
- Built audit logging system (`audit_log.json`):
  - Tracks every run: timestamp, client, keyword, platform, device, status, file paths
  - Handles corrupt/empty log files gracefully
- Implemented round-robin keyword distribution across devices (same logic as seeding):
  - All keywords from all clients distributed evenly across available devices
  - Each device runs its queue sequentially, all devices run in parallel
  - 1 device + 1 client = 1 keyword + 1 random platform per audit run
  - 13 clients × 5 keywords = 65 keywords → 65 screenshots distributed across devices
  - Random platform per keyword (Gemini/ChatGPT/Perplexity) — over multiple weekly runs, each keyword gets audited on all platforms
- Implemented `--all-devices` parallel mode:
  - Auto-discovers all connected devices from `adb devices`
  - `--exclude` flag to skip incompatible devices (e.g., `--exclude Redmi`)
  - Each device gets unique CDP port (9222 + device index) to prevent `adb forward` collisions
  - One thread per device, each running its queue sequentially
- Fixed CDP port collision in parallel mode — all devices were sharing port 9222, causing `adb forward` overwrites and failed CDP connections
- Fixed Samsung microphone permission popup on Gemini — added `find_and_tap` for "Never allow" and "Block" in `_dismiss_gemini_popups()`
- Fixed scroll offset — increased from -80 to -150 pixels to prevent #1 ranking being cut off by sticky headers on all platforms
- Fixed Perplexity CDP scroll selector — target `.prose` or `ol` (numbered list) to scroll past the collapsed prompt bubble
- Added CLI flags: `--test`, `--platform`, `--all-devices`, `--exclude`, `--clients`, `--keyword-index`
- Built Flask API endpoints for remote audit triggering:
  - `POST /audit` — triggers audit on all devices in background with round-robin distribution, returns immediately with job info
  - `GET /audit/status` — returns today's audit log entries
  - Accepts body params: platform, clients, exclude
- Updated `POST /audit` API endpoint with same distribution logic — returns distribution plan showing keywords per device
- Tested parallel audit across 5 devices (2 Infinix, 1 Nubia, 2 Samsung) — all passing Gemini audit
- Tested all 3 platform audit flows on Infinix — Gemini, ChatGPT, Perplexity all producing clean screenshots with response positioned at top
- Updated README with complete audit documentation:
  - Architecture diagram with audit files
  - Two Systems overview (Seeding vs Audit)
  - Ranking Audit section with output structure and file naming
  - API endpoints for audit (POST /audit, GET /audit/status)
  - CLI flags for audit.py
  - Audit mode for each platform flow
  - Troubleshooting for audit-specific issues

---

## Ticket 3

**Title:** AEO Automation — Multi-Brand Device Support, Ranking Extraction & Audit Reliability Hardening

**Branch:** `adb-and-appium-flow`

**Description:**
Expand the AEO audit system to reliably support 12 Android device brands in parallel, extract structured ranking positions from AI responses, and harden the end-to-end audit flow across Gemini, ChatGPT, and Perplexity. Covers brand-specific Chrome FRE handling, Sources/citation button detection, ranking regex extraction, screenshot quality fixes (maps, empty captures), ChatGPT login redirect recovery, daily session flow validation across all brands, AEOAdmin backend integration, and the `aeo_executor/` package scaffolding plus architecture diagrams.

**Acceptance Criteria:**
- Audit runs reliably in parallel across Infinix, TECNO, OPPO, Redmi, Samsung, Realme, Vivo, Itel, Nubia (9 active brands, up to 12 supported)
- Ranking positions extracted from AI responses using `[RANK: X/Y]` template and regex fallbacks
- Sources / citation buttons clicked correctly on Gemini and Perplexity across all brands
- Daily session flow (prompt + follow-up + backlink click) validated per brand
- ChatGPT login-redirect recovery in place; Perplexity Comet modal dismissed via content-desc
- Screenshots free of maps/embedded media; empty-screenshot retries in place
- Audit data (rankings, screenshots, text) posted to AEOAdmin backend API
- aeo_executor/ package scaffolding and architecture diagrams committed
- Docs updated with audit ranking flow, device guide, and troubleshooting

### Worklog — Apr 6, 2026 (Monday) — 6 hours

**Title:** AEO Automation — 12-Brand Device Support & Chrome FRE / Perplexity Handling

**Branch:** `adb-and-appium-flow`
**Commits:** `4f783f5`, `e35ea62`

- Expanded audit flow to support 12 Android device brands (Infinix, TECNO, OPPO, Redmi, Samsung, Realme, Vivo, Itel, Nubia, and 3 reserve brands) under one codebase
- Improved audit reliability on Infinix, Redmi, and Samsung:
  - Tightened Chrome FRE dismissal sequence per brand (different "Use without an account" / "No thanks" / "Got it" wording)
  - Stabilized Gemini popup handling for mic permission and app banner across brands
  - Refactored `flows_adb.py` helpers to reduce duplicated per-brand branches (−50 lines of ad-hoc handling)
- Fixed brand-specific Perplexity Comet modal and FRE handling where the previous hardcoded coordinates missed the Close button on taller/shorter screens
- Hardened Chrome launch path in `main.py` and `setup_devices.py` so parallel runs don't race on `pm clear` + intent launch
- Small `screenshot.py` fix to guard against truncated captures on slower devices
- Ran a full parallel audit across the expanded brand set to validate the FRE + Perplexity changes end-to-end

### Worklog — Apr 7, 2026 (Tuesday) — 3 hours

**Title:** AEO Automation — Ranking Extraction, Prompt Rework & Chrome Session Management

**Branch:** `adb-and-appium-flow`
**Commits:** `a9062c8`, `c94732a`, `d69d965`

- Reworked the audit prompt to force a structured ranking line (`[RANK: X/Y]`) in every AI response, while keeping the 1-screen mobile constraint
- Added ranking extraction in `audit.py`:
  - Primary regex on `[RANK: X/Y]` template
  - Fallbacks for `#N out of M`, `ranked Nth`, and similar natural-language phrasings
  - Rank + total captured into the audit log alongside file paths
- Improved Chrome session management between audit steps — clean handoff between Gemini → ChatGPT → Perplexity without leaking cookies/tabs across platforms
- Landed project config + tests + client data bundle (`c94732a`):
  - `.claude/agents/aeo.md` and `aeo-*` slash commands (assign, audit, clients, devices, health, help, reset, scrcpy, sessions, status)
  - Test harness scripts: `test_adb_flows.py`, `test_audit_gemini.py`, `test_proxy_speed.py`, `test_screenshot_cdp.py`, `test_screenshot_native.py`, `test_screenshot_stitch.py`, `test_redmi_audit.py`
  - Refreshed `clients.json`, `active_devices.json`, `device_rotation.json`, `audit_rotation.json`
  - Proxy script improvements in `proxy.py`
- Updated `aeo.md` documentation with the new audit ranking flow, per-device guide, and troubleshooting section (`d69d965`)

### Worklog — Apr 8, 2026 (Wednesday) — 8 hours

**Title:** AEO Automation — Daily Session Flow Validation & Sources/Citation Button Fixes Across Brands

**Branch:** `adb-and-appium-flow` (uncommitted)

- Ran the daily session flow (prompt + follow-up + backlink click) across all 9 connected brands in parallel and isolated brand-specific failures
- Root-caused the Gemini "Sources" button not being tapped on TECNO, OPPO, Realme, Vivo, Nubia:
  - UI dump contained invisible/zero-height duplicate "Sources" elements at `y=192`
  - Code was matching the first duplicate and tapping an empty region
  - Fix in `flows_adb.py`: skip elements with `(y2 - y1) < 5 or (x2 - x1) < 5` and require `cy > h * 0.3`
- Fixed Perplexity citation-count "10" button not being detected:
  - Width threshold was `< 100`, real element is ~120–140 px wide
  - Bumped to `< 150` and required digit + `y1 > h * 0.4`
- Fixed Nubia scroll hitting the side banner:
  - `scroll_x` was hardcoded to `15`, which landed on the edge banner
  - Changed to `int(w * 0.3)` so scrolls happen in the content column on all brands
- Fixed Gemini input tap landing on the mic icon on some brands by moving tap x from `w//2` to `int(w * 0.3)`
- Replaced the hardcoded Perplexity Comet modal dismiss tap with `find_and_tap(content_desc="Close")` so it works across screen sizes
- Itel wireless debugging kept disconnecting mid-run — diagnosed as a hardware/Wi-Fi issue, flagged to switch Itel to USB
- OPPO `pm clear` was being blocked by Permission Monitoring — documented the Developer Options workaround ("Disable Permission Monitoring") instead of adding a code hack
- Validated daily session flow end-to-end on all brands after fixes: prompt entry, follow-up, Sources/citation tap, and backlink click when the site is cited by the AI
- Started scaffolding the `aeo_executor/` package and architecture diagrams to modularize the executor:
  - `aeo_executor/{config,api,executor,device,platforms,parsing}/__init__.py`
  - `aeo_executor/device/adb.py`
  - `docs/diagrams/{architecture,execution-flow,api-contract,app-structure}.html`
  - `docs/AEO_EXECUTOR.html`

### Worklog — Apr 9, 2026 (Thursday) — 4 hours

**Title:** AEO Automation — Audit Screenshot Quality, Login-Redirect Recovery & AEOAdmin Backend Integration

**Branch:** `adb-and-appium-flow` (uncommitted)

- Ran the full audit across all 8 devices after the session-flow fixes and triaged the remaining issues
- Fixed ChatGPT screenshots showing embedded maps:
  - Added a JS cleanup step in the ChatGPT audit flow to remove `iframe`, `.mapboxgl-map`, `[class*="map"]`, `[data-testid*="map"]`, and tall `<canvas>` blocks before screencap
  - Also updated the prompt with "Do not include any maps, images, or embedded content — text only."
- Fixed empty Perplexity screenshots:
  - `screenshot.py` `take_screenshot()` now retries up to 3× with a 2 s delay when the raw buffer is `< 100` bytes
- Fixed ChatGPT login redirect (`auth.openai.com/log`) that was trapping Vivo mid-audit:
  - Added a 2-pass login detection loop checking UI text for `"Log in or sign up"`, `"auth.openai"`, `"Email address"`, `"Continue with Google"`
  - On detection: `force-stop com.android.chrome` → relaunch via `VIEW` intent with `--activity-clear-task` to `https://chatgpt.com` → `wait_for_page_ready`
  - Second check runs after a 3 s delay to catch delayed redirects (first check was passing briefly before the redirect fired)
- Integrated audit output with the AEOAdmin backend at `https://isobel-bendable-unpurely.ngrok-free.dev`:
  - Wired `POST /api/ranking-reports`, `POST /api/sessions`, `POST /api/audit-logs` from the audit pipeline
  - Corresponding backend routes updated in `AEOAdmin/artifacts/api-server/src/routes/{ranking-reports,sessions,audit-logs,index}.ts`
  - Sample payload end-to-end verified against the BE
- Re-ran audit on the 8 devices: 7/8 completed cleanly with ranking extraction (Realme, Itel, TECNO, Infinix, Nubia, Redmi, OPPO); Vivo isolated for retest with the new login-redirect double-check

---

### Worklog — Apr 27-28, 2026 (Sun-Mon) — 4 hours

**Title:** AEO Daily Session Run — 190 Pass, embedded_url Backlink Fix & GBP Matching

**Branch:** `adb-and-appium-flow` (uncommitted)

- Ran the full daily AEO session: 190 jobs, 38 campaigns, 29 clients across 10 devices
  - Main run: 140/190 PASS (74%), 6 remainder retries to reach 190
  - Final consolidated CSV: 208 rows (190 pass + 18 fail), pass_01-04 split (50/50/50/40)
  - 3 sessions never passed due to gost crashes on specific zips (campaigns 3/6/31)
- Implemented GBP `embedded_url` backlink fix in executor:
  - `run_daily_all.py`: `_normalize_link` now captures `embeddedUrl` from admin `keyword_links`; `_backlink_match_url` uses it instead of `maps.app.goo.gl` short links for AI source matching
  - `prompt_generator.py`: `_classify_backlinks` extracts `gbp_websites` domains from `embedded_url`; seeding hooks reference actual website; follow-up adds `source_request` motivation
  - Rationale: GBP short links never appear in AI source citations; the embedded website URL is what LLMs actually cite
- Refreshed `active_devices.json` with current DHCP IPs across all 10 devices
  - 7 devices had IP shifts; device-106 replaced (W001028 offline, W006788 at .120)
  - Device-104 (nubia Z2460) needed `adb tcpip 5555` to re-enable ADB over WiFi
  - Device-101 (vivo V2430) excluded from pool — persistent SocksDroid/Decodo preflight failure
- Fixed remainder plan builder: keyword_text vs keyword field mismatch between plan and CSV
- Consolidated CSV format: 208 rows matching previous pattern (Apr 24 had 240 rows, 190p+50f)
- Cleaned up intermediate CSVs, retry queues, and old April 24-25 pass files
