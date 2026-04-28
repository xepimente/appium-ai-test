"""Campaign-level daily AEO runner.

Business rule (see memory/project_daily_campaign_level.md):
  * Daily = 5 random searches per CAMPAIGN (AEO plan), not per client.
  * Random platform per session (ChatGPT / Gemini / Perplexity).
  * 50% follow-up (handled inside agents.prompt_generator).
  * Scheduling constraint: within one wave (concurrent device slots), no two
    jobs may share the same client_id or the same campaign_id. Prevents
    IP/fingerprint correlation that platforms flag as bot behavior.

Pipeline:
  fetch admin state
    -> build jobs (5 per active-keywords plan, random platform)
    -> pre-generate DeepSeek prompts+followups (uses real backlinks)
    -> greedy pack into waves (len(device_pool) slots, client+campaign unique)
    -> per wave: gost preflight -> run_parallel -> CSV rows
  -> write sessions_log.daily.csv (this run) + append to sessions_log.all.csv
  -> write retry_queue.csv for failed sessions

Usage:
  python3 run_daily_all.py                         # full run
  python3 run_daily_all.py --dry-run               # print wave plan, exit
  python3 run_daily_all.py --limit-plans=3         # smoke test (3 campaigns)
  python3 run_daily_all.py --client=6              # single client only
  python3 run_daily_all.py --sessions-per-campaign=5
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

import requests

from agents.prompt_generator import generate_session_prompts, get_last_backlink_injected
from gost_manager import GostManager
from session_runner import run_parallel


ADMIN = "https://jjm59vpn3y.us-east-1.awsapprunner.com"
TOKEN = "89d0385d06ab80d79a034745c978a298f4728c85066616f359cb8b9bb87e5644"
H = {"X-Executor-Token": TOKEN, "Content-Type": "application/json"}

PLATFORMS = ("ChatGPT", "Gemini", "Perplexity")
SESSIONS_PER_CAMPAIGN_DEFAULT = 5

SESSION_CSV_COLUMNS = [
    "timestamp", "date", "wave_index",
    "client_id", "client_name", "biz_name", "search_address",
    "campaign_id", "campaign_name", "keyword",
    "prompt", "follow_up", "has_follow_up",
    "device_id", "platform", "status", "duration_s",
    "proxy_status", "proxy_username", "proxy_host", "proxy_port",
    # Removed empty-in-practice: city, state (null on admin — replaced by
    # search_address which carries full address string). Also dropped
    # proxy_ip, proxy_city, proxy_region, proxy_country, proxy_zip (flaky
    # ipinfo lookup via tunnel).
    "base_latitude", "base_longitude",
    "mocked_latitude", "mocked_longitude", "mocked_timezone",
    "backlinks_expected", "backlink_injected", "backlink_found", "backlink_url",
    "failure_step", "error",
]

# Shared all-time CSV (existing schema, no wave_index) — kept in parity with
# run_client_daily.py / run_from_json.py so push_sessions_to_admin.py works.
ALL_CSV_COLUMNS = [c for c in SESSION_CSV_COLUMNS if c not in ("wave_index", "failure_step")]

RETRY_CSV_COLUMNS = [
    "timestamp", "wave_index", "client_id", "campaign_id", "keyword_id",
    "keyword", "platform", "failure_step", "error",
]


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Job:
    client_id: int
    client_name: str
    campaign_id: int
    campaign_name: str
    business_id: int
    keyword_id: int
    keyword_text: str
    platform: str
    biz_name: str
    biz_city: str
    biz_state: str
    biz_zip: str | None
    biz_lat: float
    biz_lng: float
    biz_timezone: str
    biz_address: str
    gmb_url: str | None
    backlinks: tuple  # tuple of dicts ({"url","type","title","topic","domain","published_at"})
    prompt: str = ""
    follow_up: str = ""
    backlink_injected: bool = False  # True if backlink hooks were seeded in the prompt


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_zip(addr: str | None) -> str | None:
    if not addr:
        return None
    m = re.search(r"\b(\d{5})(?:-\d{4})?\b", addr)
    return m.group(1) if m else None


def _slugify(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in (s or "").lower()).strip("_")


def _session_id_from_username(username: str | None) -> str | None:
    if not username:
        return None
    try:
        return username.split("session-", 1)[1].split("-", 1)[0]
    except Exception:
        return None


def _normalize_link(link: dict) -> dict:
    """Admin keyword.links[] → prompt_generator backlink dict."""
    label = (link.get("linkTypeLabel") or "").lower()
    url = link.get("linkUrl") or ""
    kind = "gbp" if ("gbp" in label or "share.google" in url) else "article"
    embedded = link.get("embeddedUrl") or ""
    return {
        "url": url,
        "type": kind,
        "title": link.get("linkTitle"),
        "topic": link.get("linkTopic"),
        "domain": None,
        "published_at": link.get("linkPublishedAt"),
        "embedded_url": embedded,
    }


def _backlink_match_url(bl: dict) -> str:
    """Return the URL to search for in AI sources. For GBP links, use the
    embedded_url (the actual website inside the GBP) since maps.app.goo.gl
    short links never appear in AI source citations."""
    if bl.get("type") == "gbp" and bl.get("embedded_url"):
        return bl["embedded_url"]
    return bl["url"]


def classify_failure(error: str | None, steps: list | None) -> str:
    """Map raw error/step trail to a coarse failure_step bucket used by retry."""
    err = (error or "").lower()
    if "preflight" in err or "upstream" in err or "decodo" in err:
        return "preflight_failed"
    if "login" in err or "sign in" in err or "sign-in" in err:
        return "login_wall_blocked"
    if "followup" in err or "follow_up" in err or "follow-up" in err:
        return "followup_timeout"
    if "timeout" in err or "wait_for_response" in err:
        return "generation_timeout"
    if "submit" in err or "send" in err:
        return "submit_failed"
    if err:
        return "other"
    return "unknown"


# ── Admin fetch + planner ──────────────────────────────────────────────────────

def fetch_admin_state() -> tuple[list, list, list, list]:
    clients = requests.get(f"{ADMIN}/api/clients", headers=H, timeout=30).json()
    businesses = requests.get(f"{ADMIN}/api/businesses", headers=H, timeout=30).json()
    plans = requests.get(f"{ADMIN}/api/aeo-plans", headers=H, timeout=30).json()
    keywords = requests.get(f"{ADMIN}/api/keywords", headers=H, timeout=30).json()
    return clients, businesses, plans, keywords


def _load_geocode_cache() -> dict:
    """Load geocode_cache.json — maps searchAddress → [lat, lng].
    Populated by geocode_campaigns.py (Nominatim + manual fallback)."""
    import json as _json
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "geocode_cache.json")
    if os.path.exists(path):
        try:
            return _json.load(open(path))
        except Exception:
            return {}
    return {}


def build_jobs(
    clients: list,
    businesses: list,
    plans: list,
    keywords: list,
    *,
    sessions_per_campaign: int = SESSIONS_PER_CAMPAIGN_DEFAULT,
    client_filter: int | None = None,
    limit_plans: int | None = None,
) -> list[Job]:
    """Expand each active campaign into N jobs (random kw × random platform).

    A "campaign" here = one AEO plan (plans.id). A plan is considered active if
    it has >=1 active keyword. All 37 current plans qualify.
    """
    client_by_id = {c["id"]: c for c in clients}
    biz_by_id = {b["id"]: b for b in businesses}
    geocode = _load_geocode_cache()
    kws_by_plan: dict[int, list] = {}
    for k in keywords:
        if not k.get("isActive"):
            continue
        pid = k.get("aeoPlanId")
        if pid is None:
            continue
        kws_by_plan.setdefault(pid, []).append(k)

    selected_plans = [p for p in plans if kws_by_plan.get(p["id"])]
    if client_filter is not None:
        selected_plans = [p for p in selected_plans if p["clientId"] == client_filter]
    if limit_plans is not None:
        selected_plans = selected_plans[:limit_plans]

    jobs: list[Job] = []
    for plan in selected_plans:
        plan_kws = kws_by_plan[plan["id"]]
        pick = random.sample(plan_kws, min(sessions_per_campaign, len(plan_kws)))
        biz = biz_by_id.get(plan["businessId"]) or {}
        client = client_by_id.get(plan["clientId"]) or {}
        biz_addr = plan.get("searchAddress") or biz.get("publishedAddress") or ""
        biz_zip = _extract_zip(biz_addr)

        # Resolve base lat/lng from geocode cache (keyed by searchAddress).
        # Admin's biz.latitude/longitude are null for all businesses as of 2026-04-22,
        # so the cache is the authoritative source. Falls back to biz.latitude
        # if somehow populated, else (0.0, 0.0) marker for "missing".
        cached_coords = geocode.get(biz_addr) if biz_addr else None
        if cached_coords and len(cached_coords) == 2:
            base_lat, base_lng = float(cached_coords[0]), float(cached_coords[1])
        else:
            base_lat = float(biz.get("latitude") or 0.0)
            base_lng = float(biz.get("longitude") or 0.0)

        for kw in pick:
            backlinks = tuple(
                _normalize_link(l) for l in (kw.get("links") or [])
                if l.get("linkActive") and l.get("linkUrl")
            )
            jobs.append(Job(
                client_id=plan["clientId"],
                client_name=client.get("businessName") or f"client-{plan['clientId']}",
                campaign_id=plan["id"],
                campaign_name=plan["name"],
                business_id=plan["businessId"],
                keyword_id=kw["id"],
                keyword_text=kw["keywordText"],
                platform=random.choice(PLATFORMS),
                biz_name=biz.get("name") or plan.get("businessName") or "",
                biz_city=biz.get("city") or "",
                biz_state=biz.get("state") or "",
                biz_zip=biz_zip,
                biz_lat=base_lat,
                biz_lng=base_lng,
                biz_timezone=biz.get("timezone") or "America/New_York",
                biz_address=biz_addr,
                gmb_url=biz.get("gmbUrl"),
                backlinks=backlinks,
            ))
    return jobs


# ── Prompt pre-generation ──────────────────────────────────────────────────────

def _fallback_prompt(job: Job) -> tuple[str, str | None]:
    base = (
        f"I'm looking for recommendations on {job.keyword_text} in the "
        f"{job.biz_city}, {job.biz_state} area. A friend mentioned {job.biz_name}. "
        f"Are they a solid choice for {job.keyword_text}, or are there stronger "
        f"options locally? If you can, cite the sources or links you're using."
    )
    follow = f"Got any specific examples of their recent work or reviews I can check?"
    if random.random() < 0.5:
        follow = None
    return base, follow


def hydrate_prompts(jobs: list[Job], use_llm: bool = True) -> list[Job]:
    """Fill job.prompt + job.follow_up. Falls back to hardcoded template when
    DEEPSEEK_API_KEY is unset or a call fails. 50% skip for follow-up is
    handled inside generate_session_prompts."""
    have_key = bool(os.environ.get("DEEPSEEK_API_KEY"))
    hydrated: list[Job] = []
    for i, job in enumerate(jobs, 1):
        prompt, follow = "", ""
        backlink_injected = False
        if use_llm and have_key:
            client_ctx = {
                "biz_name": job.biz_name,
                "biz_category": "",
                "city": job.biz_city,
                "state": job.biz_state,
                "biz_address": job.biz_address,
                "gmb_url": job.gmb_url,
            }
            kw_ctx = {
                "text": job.keyword_text,
                "backlinks": list(job.backlinks),
            }
            try:
                prompt, follow = generate_session_prompts(
                    client_ctx, kw_ctx, platform=job.platform.lower()
                )
                backlink_injected = bool(get_last_backlink_injected())
            except Exception as e:
                print(f"  [warn] DeepSeek failed for kid={job.keyword_id} ({e}); using fallback")
                prompt, follow = _fallback_prompt(job)
        else:
            prompt, follow = _fallback_prompt(job)
        hydrated.append(replace(job, prompt=prompt, follow_up=follow or "", backlink_injected=backlink_injected))
        if i % 10 == 0:
            print(f"  ...hydrated {i}/{len(jobs)} prompts")
    return hydrated


# ── Plan JSON serialization ────────────────────────────────────────────────────

def _job_to_dict(job: Job) -> dict:
    d = asdict(job)
    d["backlinks"] = list(job.backlinks)  # tuple → list for JSON
    return d


def _dict_to_job(d: dict) -> Job:
    fields = {**d, "backlinks": tuple(d.get("backlinks") or ())}
    return Job(**fields)


def save_plan(waves: list[list[Job]], path: str) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "total_jobs":   sum(len(w) for w in waves),
        "waves":        [[_job_to_dict(j) for j in wave] for wave in waves],
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def load_plan(path: str) -> list[list[Job]]:
    with open(path) as f:
        payload = json.load(f)
    return [[_dict_to_job(d) for d in wave] for wave in payload["waves"]]


# ── Wave packer ────────────────────────────────────────────────────────────────

def pack_waves(jobs: list[Job], slots: int) -> list[list[Job]]:
    """Greedy wave packing.

    Constraint: within one wave, no two jobs may share client_id OR
    campaign_id. Overflow spills to next wave. Each wave has at most `slots`
    jobs (the device pool size).
    """
    pending = list(jobs)
    random.shuffle(pending)
    waves: list[list[Job]] = []
    while pending:
        wave: list[Job] = []
        used_clients: set[int] = set()
        used_campaigns: set[int] = set()
        remaining: list[Job] = []
        for job in pending:
            if len(wave) >= slots:
                remaining.append(job)
                continue
            if job.client_id in used_clients or job.campaign_id in used_campaigns:
                remaining.append(job)
                continue
            wave.append(job)
            used_clients.add(job.client_id)
            used_campaigns.add(job.campaign_id)
        waves.append(wave)
        pending = remaining
    return waves


def pack_batches(
    jobs: list[Job],
    batch_size: int = 25,
    wave_sizes: list[int] | None = None,
    device_count: int = 5,
) -> list[list[list[Job]]]:
    """Two-level packing: batches of `batch_size` (distinct-campaign guarantee),
    each batch split into waves whose sizes cycle through `wave_sizes`.

    Returns: list of batches; each batch is a list of waves; each wave is a
    list of jobs (<= max(wave_sizes)).

    `wave_sizes` defaults to [device_count] (single size = current behaviour).
    Pass e.g. [3, 2] to alternate 3-phone and 2-phone sub-waves. Each wave
    size must be <= device_count.

    Constraint: within a batch, no two jobs share campaign_id. Client repeats
    are allowed (clients 4/5/21/27 have multiple campaigns — strict client
    uniqueness is infeasible over 8 batches with 190 jobs).
    """
    if not wave_sizes:
        wave_sizes = [device_count]
    if any(s > device_count for s in wave_sizes):
        raise ValueError(f"wave_sizes {wave_sizes} exceed device_count {device_count}")

    pending = list(jobs)
    random.shuffle(pending)
    batches_flat: list[list[Job]] = []
    while pending:
        batch: list[Job] = []
        used_campaigns: set[int] = set()
        remaining: list[Job] = []
        for job in pending:
            if len(batch) >= batch_size:
                remaining.append(job)
                continue
            if job.campaign_id in used_campaigns:
                remaining.append(job)
                continue
            batch.append(job)
            used_campaigns.add(job.campaign_id)
        batches_flat.append(batch)
        pending = remaining

    # Split each batch into waves whose sizes cycle through wave_sizes.
    # Since all campaigns in a batch are distinct, any slicing yields
    # distinct-campaign waves.
    batches: list[list[list[Job]]] = []
    for batch in batches_flat:
        waves: list[list[Job]] = []
        idx = 0
        size_cycle = 0
        while idx < len(batch):
            size = wave_sizes[size_cycle % len(wave_sizes)]
            waves.append(batch[idx:idx + size])
            idx += size
            size_cycle += 1
        batches.append(waves)
    return batches


# ── Wave runner ────────────────────────────────────────────────────────────────

def _build_wave_sessions_and_specs(
    wave: list[Job],
    device_pool: dict,
    device_ids: list[str],
) -> tuple[list[dict], list[dict]]:
    sessions: list[dict] = []
    specs: list[dict] = []
    for i, job in enumerate(wave):
        did = device_ids[i]
        dev = device_pool[did]
        gost_key = f"w{i}-{did}-{job.campaign_id}-{job.keyword_id}"
        specs.append({
            "device_id": gost_key,
            "zip": job.biz_zip or "10001",
            "state": job.biz_state or "",
            "country": "us",
            "session_duration": 30,
        })
        proxy_cfg = {
            "country": "us",
            "session_duration": 30,
            "zip": job.biz_zip or "10001",
            "timezone": job.biz_timezone or "America/New_York",
            "latitude": job.biz_lat,
            "longitude": job.biz_lng,
            "_gost_key": gost_key,
        }
        sessions.append({
            "device_id":   did,
            "serial":      dev["serial"],
            "full_serial": dev["serial"],
            "port":        dev["port"],
            "platform":    job.platform,
            "platforms":   [job.platform],
            "prompt":      job.prompt,
            "follow_up":   job.follow_up,
            "use_adb":     dev.get("use_adb", True),
            "backlinks":   [_backlink_match_url(b) for b in job.backlinks],
            "proxy":       proxy_cfg,
        })
    return sessions, specs


def run_wave(
    wave: list[Job],
    wave_index: int,
    device_pool: dict,
    device_ids: list[str],
) -> list[tuple[Job, dict]]:
    """Run one wave. Returns list of (job, result) tuples.

    Uses only the first len(wave) devices — so waves smaller than the pool
    leave the extra phones idle (used intentionally in 3-2 alternation)."""
    sessions, specs = _build_wave_sessions_and_specs(wave, device_pool, device_ids[:len(wave)])
    print(f"\n[wave {wave_index}] spinning gost for {len(sessions)} session(s)...")
    gost = GostManager(specs, base_port=11001)
    gost.start(wait_seconds=2.0)
    for sess in sessions:
        key = sess["proxy"].pop("_gost_key")
        sess["proxy"]["gost"] = gost.mapping[key]
    t0 = time.time()
    try:
        results = run_parallel(sessions)
    finally:
        gost.stop()
    elapsed = time.time() - t0
    ok = sum(1 for r in results if r.get("success"))
    print(f"[wave {wave_index}] done in {elapsed:.1f}s — {ok}/{len(results)} OK")
    return list(zip(wave, results))


# ── Rolling worker-pool runner ─────────────────────────────────────────────────

def _build_single_session_and_spec(
    job: Job,
    device_id: str,
    device_pool: dict,
    gost_key: str,
) -> tuple[dict, dict]:
    dev = device_pool[device_id]
    spec = {
        "device_id": gost_key,
        "zip": job.biz_zip or "10001",
        "state": job.biz_state or "",
        "country": "us",
        "session_duration": 30,
    }
    proxy_cfg = {
        "country": "us",
        "session_duration": 30,
        "zip": job.biz_zip or "10001",
        "timezone": job.biz_timezone or "America/New_York",
        "latitude": job.biz_lat,
        "longitude": job.biz_lng,
        "_gost_key": gost_key,
    }
    session = {
        "device_id":   device_id,
        "serial":      dev["serial"],
        "full_serial": dev["serial"],
        "port":        dev["port"],
        "platform":    job.platform,
        "platforms":   [job.platform],
        "prompt":      job.prompt,
        "follow_up":   job.follow_up,
        "use_adb":     dev.get("use_adb", True),
        "backlinks":   [_backlink_match_url(b) for b in job.backlinks],
        "proxy":       proxy_cfg,
    }
    return session, spec


def _run_single_session(
    job: Job,
    device_id: str,
    device_pool: dict,
    listen_port: int,
    gost_key: str,
) -> dict:
    """Spin a 1-listener gost, run a single session, tear gost down.

    Returns the result dict (same shape as run_parallel's list items)."""
    session, spec = _build_single_session_and_spec(job, device_id, device_pool, gost_key)
    gost = GostManager([spec], base_port=listen_port)
    gost.start(wait_seconds=2.0)
    session["proxy"].pop("_gost_key", None)
    session["proxy"]["gost"] = gost.mapping[gost_key]
    try:
        results = run_parallel([session])
    finally:
        gost.stop()
    return results[0]


def run_rolling(
    jobs: list[Job],
    device_pool: dict,
    device_ids: list[str],
    max_concurrent: int,
    start_seq: int,
    on_result=None,
) -> list[tuple[Job, dict, int]]:
    """Rolling worker pool over a flat job list.

    * Up to `max_concurrent` devices run at once (<= len(device_ids)).
    * When a session finishes, its device is freed and the next dispatchable
      job is picked immediately — no waiting for all concurrents to finish.
    * Campaign-distinct constraint: no two concurrent sessions share campaign_id.

    Returns list of (job, result, seq_idx) tuples."""
    from collections import deque
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

    if max_concurrent < 1:
        raise ValueError(f"max_concurrent must be >= 1 (got {max_concurrent})")
    max_concurrent = min(max_concurrent, len(device_ids))

    # Stable per-device listener port so parallel gosts never collide.
    device_listen_port = {did: 11001 + 10 * i for i, did in enumerate(device_ids)}

    pending = list(jobs)
    # FIFO: finished devices go to the back, next dispatch pulls from front.
    # Ensures fair rotation across ALL device_ids rather than re-picking the
    # phone that just finished.
    free: deque[str] = deque(device_ids)
    active_campaigns: set[int] = set()
    running: dict = {}  # future -> (device_id, job, seq_idx)
    results: list[tuple[Job, dict, int]] = []
    seq = 0
    gost_counter = 0

    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        while pending or running:
            # Dispatch phase — fill up to max_concurrent slots
            while pending and free and len(running) < max_concurrent:
                job_idx = next(
                    (i for i, j in enumerate(pending)
                     if j.campaign_id not in active_campaigns),
                    None,
                )
                if job_idx is None:
                    break  # all remaining pending jobs share an active campaign
                job = pending.pop(job_idx)
                device = free.popleft()
                active_campaigns.add(job.campaign_id)
                seq += 1
                gost_counter += 1
                seq_idx = start_seq + seq
                gost_key = f"roll-{gost_counter}-{device}-{job.campaign_id}"
                print(f"\n[roll #{seq_idx}] dispatch {device} ← "
                      f"c{job.client_id}/p{job.campaign_id} kw={job.keyword_text!r} "
                      f"→ {job.platform}  "
                      f"(running={len(running)+1}/{max_concurrent}, "
                      f"free={len(free)}, pending={len(pending)})")
                fut = executor.submit(
                    _run_single_session, job, device, device_pool,
                    device_listen_port[device], gost_key,
                )
                running[fut] = (device, job, seq_idx)

            if not running:
                if pending:
                    print(f"[warn] {len(pending)} jobs unreachable — "
                          f"campaigns all blocked and nothing running")
                break

            done_set, _ = wait(list(running.keys()), return_when=FIRST_COMPLETED)
            for fut in done_set:
                device, job, seq_idx = running.pop(fut)
                # Append to tail → fair rotation across all phones
                free.append(device)
                active_campaigns.discard(job.campaign_id)
                try:
                    result = fut.result()
                except Exception as e:
                    result = {
                        "success": False,
                        "error": str(e),
                        "device_id": device,
                        "platform_results": [{"error": str(e), "duration_s": 0}],
                    }
                ok = result.get("success")
                pr = (result.get("platform_results") or [{}])[0]
                mark = "PASS" if ok else "FAIL"
                print(f"[roll #{seq_idx}] {mark} {device} {job.platform} "
                      f"{pr.get('duration_s', 0)}s  "
                      f"err={(pr.get('error') or '')[:60]}")
                results.append((job, result, seq_idx))
                if on_result is not None:
                    try:
                        on_result(job, result, seq_idx)
                    except Exception as e:
                        print(f"[roll #{seq_idx}] warn: on_result callback failed: {e}")

    # Sort results by seq_idx for deterministic CSV ordering
    results.sort(key=lambda t: t[2])
    return results


# ── CSV output ─────────────────────────────────────────────────────────────────

def _csv_row_from_result(job: Job, result: dict, wave_index: int) -> dict:
    pr = (result.get("platform_results") or [{}])[0]
    steps = pr.get("steps") or []
    clicked = next(
        (s for s in steps if isinstance(s, str) and s.startswith("backlink_clicked:")),
        None,
    )
    proxy_info = result.get("proxy") or {}
    err = pr.get("error", "") or result.get("error", "")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    today = datetime.now(timezone.utc).date().isoformat()
    return {
        "timestamp":          now,
        "date":               today,
        "wave_index":         wave_index,
        "client_id":          job.client_id,
        "client_name":        job.client_name,
        "biz_name":           job.biz_name,
        "search_address":     job.biz_address,
        "campaign_id":        job.campaign_id,
        "campaign_name":      job.campaign_name,
        "keyword":            job.keyword_text,
        "prompt":             job.prompt,
        "follow_up":          job.follow_up,
        "has_follow_up":      bool(job.follow_up),
        "device_id":          result.get("device_id", ""),
        "platform":           job.platform,
        "status":             "success" if result.get("success") else "error",
        "duration_s":         pr.get("duration_s", ""),
        "proxy_status":       proxy_info.get("status"),
        "proxy_username":     proxy_info.get("username"),
        "proxy_host":         proxy_info.get("proxy_host"),
        "proxy_port":         proxy_info.get("proxy_port"),
        "proxy_ip":           proxy_info.get("ip"),
        "proxy_city":         proxy_info.get("ip_city"),
        "proxy_region":       proxy_info.get("ip_region"),
        "proxy_country":      proxy_info.get("ip_country"),
        "proxy_zip":          proxy_info.get("ip_zip"),
        "base_latitude":      proxy_info.get("base_latitude"),
        "base_longitude":     proxy_info.get("base_longitude"),
        "mocked_latitude":    proxy_info.get("mocked_latitude"),
        "mocked_longitude":   proxy_info.get("mocked_longitude"),
        "mocked_timezone":    proxy_info.get("mocked_timezone"),
        "backlinks_expected": len(job.backlinks) if job.backlink_injected else 0,
        "backlink_injected":  bool(job.backlink_injected),
        "backlink_found":     bool(clicked),
        "backlink_url":       clicked.split(":", 1)[1] if clicked else "",
        "failure_step":       "" if result.get("success") else classify_failure(err, steps),
        "error":              err,
    }


def write_csvs(rows: list[dict], daily_path: str, all_path: str, retry_path: str) -> None:
    with open(daily_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SESSION_CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    all_exists = os.path.exists(all_path)
    with open(all_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ALL_CSV_COLUMNS, extrasaction="ignore")
        if not all_exists:
            w.writeheader()
        w.writerows(rows)

    failed = [r for r in rows if r["status"] != "success"]
    if failed:
        retry_rows = [{
            "timestamp":    r["timestamp"],
            "wave_index":   r["wave_index"],
            "client_id":    r["client_id"],
            "campaign_id":  r["campaign_id"],
            "keyword_id":   "",  # populated in build_jobs pass below
            "keyword":      r["keyword"],
            "platform":     r["platform"],
            "failure_step": r["failure_step"],
            "error":        r["error"],
        } for r in failed]
        with open(retry_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=RETRY_CSV_COLUMNS)
            w.writeheader()
            w.writerows(retry_rows)


# ── Dry-run printer ────────────────────────────────────────────────────────────

def print_plan_summary(jobs: list[Job], waves: list[list[Job]]) -> None:
    print(f"\n{'='*70}\nPLAN SUMMARY")
    print(f"  jobs:      {len(jobs)}")
    print(f"  campaigns: {len({j.campaign_id for j in jobs})}")
    print(f"  clients:   {len({j.client_id for j in jobs})}")
    print(f"  waves:     {len(waves)}")
    print(f"{'='*70}")
    for i, wave in enumerate(waves, 1):
        print(f"  wave {i:>2} ({len(wave)} slot{'s' if len(wave)!=1 else ''}):")
        for job in wave:
            bl = len(job.backlinks)
            print(f"    - c{job.client_id:>2}/p{job.campaign_id:>2}  {job.platform:<11} "
                  f"kw={job.keyword_text[:26]:<26} zip={job.biz_zip or '-':<5} bl={bl}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Campaign-level daily AEO runner")
    parser.add_argument("--dry-run", action="store_true", help="Print wave plan and exit (no LLM, no devices)")
    parser.add_argument("--generate-only", metavar="PATH",
                        help="Hydrate prompts + save plan to JSON, do NOT run devices")
    parser.add_argument("--load", metavar="PATH",
                        help="Load a previously generated plan JSON and run it (no admin fetch, no hydration)")
    parser.add_argument("--limit-plans", type=int, default=None, help="Only process first N plans")
    parser.add_argument("--client", type=int, default=None, help="Filter to one clientId")
    parser.add_argument("--sessions-per-campaign", type=int, default=SESSIONS_PER_CAMPAIGN_DEFAULT)
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument("--no-llm", action="store_true", help="Skip DeepSeek, use hardcoded template")
    parser.add_argument("--batch-size", type=int, default=25,
                        help="Sessions per batch (pause between batches). Default 25 (5 devices × 5 rounds).")
    parser.add_argument("--wave-sizes", type=str, default="5",
                        help="Comma-separated wave sizes that cycle within a batch. e.g. '3,2' = alternate "
                             "3-phone and 2-phone sub-waves. Default '5' = use all 5 phones each wave.")
    parser.add_argument("--start-batch", type=int, default=1,
                        help="Resume from batch N (1-indexed). Useful after partial run.")
    parser.add_argument("--no-pause", action="store_true",
                        help="Don't wait for user confirmation between batches — run straight through.")
    parser.add_argument("--rolling", type=int, default=0,
                        help="Rolling dispatch mode: max N concurrent sessions (ignores --wave-sizes). "
                             "Sessions start as soon as any device frees up, instead of waiting for "
                             "full waves to finish. e.g. --rolling=3 keeps max 3 phones busy.")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)
    else:
        random.seed()

    # --load: skip planner + hydration, re-pack into batches, run with pauses
    if args.load:
        print(f"Loading plan from {args.load}...")
        loaded_waves = load_plan(args.load)
        all_jobs = [j for w in loaded_waves for j in w]
        print(f"  total jobs: {len(all_jobs)}")

        with open("active_devices.json") as f:
            device_pool = json.load(f)
        device_ids = list(device_pool.keys())
        print(f"  device pool: {len(device_ids)} -> {', '.join(device_ids)}")

        wave_sizes = [int(s.strip()) for s in args.wave_sizes.split(",") if s.strip()]
        batches = pack_batches(
            all_jobs, batch_size=args.batch_size,
            wave_sizes=wave_sizes, device_count=len(device_ids),
        )
        total_in_batches = sum(len(w) for b in batches for w in b)
        print(f"  batches: {len(batches)} (batch_size={args.batch_size}, "
              f"wave_sizes={wave_sizes}, {len(device_ids)} devices)")
        for bi, batch_waves in enumerate(batches, 1):
            bn = sum(len(w) for w in batch_waves)
            campaigns = {j.campaign_id for w in batch_waves for j in w}
            print(f"    batch {bi}: {bn} sessions, {len(campaigns)} distinct campaigns")
        assert total_in_batches == len(all_jobs), "pack lost jobs"

        daily_path = f"sessions_log.daily_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        retry_path = f"retry_queue_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        all_rows: list[dict] = []
        t_start = time.time()
        global_wave_idx = 0

        for bi, batch_waves in enumerate(batches, 1):
            if bi < args.start_batch:
                global_wave_idx += len(batch_waves) if not args.rolling \
                                  else sum(len(w) for w in batch_waves)
                print(f"\n[skip] batch {bi}/{len(batches)} (--start-batch={args.start_batch})")
                continue
            batch_total = sum(len(w) for w in batch_waves)
            batch_t0 = time.time()

            if args.rolling:
                print(f"\n{'='*70}\nBATCH {bi}/{len(batches)} — {batch_total} sessions "
                      f"(rolling mode, max {args.rolling} concurrent)\n{'='*70}")
                flat_jobs = [j for w in batch_waves for j in w]

                def _save_row(job, result, seq_idx, _rows=all_rows,
                              _dp=daily_path, _rp=retry_path):
                    _rows.append(_csv_row_from_result(job, result, seq_idx))
                    write_csvs(_rows, _dp, "sessions_log.all.csv", _rp)

                run_rolling(
                    flat_jobs, device_pool, device_ids,
                    max_concurrent=args.rolling,
                    start_seq=global_wave_idx,
                    on_result=_save_row,
                )
                global_wave_idx += len(flat_jobs)
            else:
                print(f"\n{'='*70}\nBATCH {bi}/{len(batches)} — {batch_total} sessions "
                      f"({len(batch_waves)} waves × up to {len(device_ids)} devices)\n{'='*70}")
                # Rotate device order per sub-wave so fewer-device sub-waves
                # (e.g. size 3 of 5) don't always use the same 3 phones.
                device_cursor = 0
                for wi, wave in enumerate(batch_waves, 1):
                    global_wave_idx += 1
                    n = len(wave)
                    rotated = [device_ids[(device_cursor + k) % len(device_ids)] for k in range(n)]
                    device_cursor = (device_cursor + n) % len(device_ids)
                    print(f"\n--- batch {bi} wave {wi}/{len(batch_waves)} "
                          f"(global #{global_wave_idx}, size={n}, phones={rotated}) ---")
                    for j in wave:
                        print(f"  slot: c{j.client_id}/p{j.campaign_id} kw={j.keyword_text!r} → {j.platform}")
                    pairs = run_wave(wave, global_wave_idx, device_pool, rotated)
                    for job, result in pairs:
                        all_rows.append(_csv_row_from_result(job, result, global_wave_idx))
                    # Incremental save after every wave so a crash never loses data
                    write_csvs(all_rows, daily_path, "sessions_log.all.csv", retry_path)
            batch_elapsed = time.time() - batch_t0
            batch_ok = sum(1 for r in all_rows[-batch_total:] if r["status"] == "success")
            print(f"\n[batch {bi}] done — {batch_ok}/{batch_total} PASS — {batch_elapsed/60:.1f}m")
            print(f"  csv so far: {daily_path} ({len(all_rows)} rows)")

            if bi < len(batches) and not args.no_pause:
                try:
                    ans = input(f"\n>>> Proceed to batch {bi+1}/{len(batches)}? [y/N]: ").strip().lower()
                except EOFError:
                    ans = "n"
                if ans != "y":
                    print(f"Stopping after batch {bi}. Run again with "
                          f"--start-batch={bi+1} --load={args.load} to resume.")
                    break

        elapsed = time.time() - t_start
        ok = sum(1 for r in all_rows if r["status"] == "success")
        fail = len(all_rows) - ok
        print(f"\n{'='*70}\nFINAL — {ok}/{len(all_rows)} PASS — elapsed {elapsed/60:.1f}m")
        print(f"  daily csv: {daily_path}")
        if fail:
            print(f"  retry queue ({fail} sessions): {retry_path}")
        print(f"{'='*70}")
        return 0 if fail == 0 else 3

    print("Fetching admin state...")
    clients, businesses, plans, keywords = fetch_admin_state()
    print(f"  clients={len(clients)}  businesses={len(businesses)}  "
          f"plans={len(plans)}  keywords={len(keywords)}")

    jobs = build_jobs(
        clients, businesses, plans, keywords,
        sessions_per_campaign=args.sessions_per_campaign,
        client_filter=args.client,
        limit_plans=args.limit_plans,
    )
    if not jobs:
        print("No jobs produced. Check filters.")
        return 1

    with open("active_devices.json") as f:
        device_pool = json.load(f)
    device_ids = list(device_pool.keys())
    print(f"  device pool: {len(device_ids)} -> {', '.join(device_ids)}")

    waves = pack_waves(jobs, slots=len(device_ids))

    if args.dry_run:
        print("(dry-run: skipping prompt hydration)")
        print_plan_summary(jobs, waves)
        return 0

    print(f"\nHydrating {len(jobs)} prompts via DeepSeek...")
    hydrated_jobs = hydrate_prompts(jobs, use_llm=not args.no_llm)
    job_by_id = {(j.campaign_id, j.keyword_id, j.platform): j for j in hydrated_jobs}
    waves = [
        [job_by_id[(j.campaign_id, j.keyword_id, j.platform)] for j in wave]
        for wave in waves
    ]
    print_plan_summary(hydrated_jobs, waves)

    if args.generate_only:
        save_plan(waves, args.generate_only)
        with_follow = sum(1 for j in hydrated_jobs if j.follow_up)
        print(f"\n{'='*70}\nPLAN SAVED → {args.generate_only}")
        print(f"  total jobs:       {len(hydrated_jobs)}")
        print(f"  with followup:    {with_follow}  ({with_follow/len(hydrated_jobs)*100:.0f}%)")
        print(f"  no followup:      {len(hydrated_jobs) - with_follow}")
        print(f"  next:  python3 run_daily_all.py --load={args.generate_only}")
        print(f"{'='*70}")
        return 0

    print(f"\n{'='*70}\nRUNNING {len(waves)} WAVES\n{'='*70}")
    all_rows: list[dict] = []
    t_start = time.time()
    for wi, wave in enumerate(waves, 1):
        print(f"\n--- wave {wi}/{len(waves)} ---")
        for j in wave:
            print(f"  slot: c{j.client_id}/p{j.campaign_id} kw={j.keyword_text!r} → {j.platform}")
        pairs = run_wave(wave, wi, device_pool, device_ids)
        for job, result in pairs:
            all_rows.append(_csv_row_from_result(job, result, wi))
    elapsed = time.time() - t_start

    daily_path = f"sessions_log.daily_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    all_path = "sessions_log.all.csv"
    retry_path = f"retry_queue_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    write_csvs(all_rows, daily_path, all_path, retry_path)

    ok = sum(1 for r in all_rows if r["status"] == "success")
    print(f"\n{'='*70}\nFINAL — {ok}/{len(all_rows)} PASS — elapsed {elapsed/60:.1f}m")
    print(f"  daily csv: {daily_path}")
    print(f"  appended:  {all_path}")
    fail = len(all_rows) - ok
    if fail:
        print(f"  retry queue ({fail} sessions): {retry_path}")
    print(f"{'='*70}")
    return 0 if fail == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
