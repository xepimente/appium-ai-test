"""Resume audit push — idempotent.

Compares audit_log.csv against reports already in admin for today,
pushes only rows missing by (clientId, keywordId, platform).
Then PATCHes runId=6 to completed.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


BASE = os.environ.get("API_BASE", "https://jjm59vpn3y.us-east-1.awsapprunner.com").rstrip("/")
TOKEN = os.environ.get("EXECUTOR_TOKEN")
if not TOKEN:
    sys.exit(
        "EXECUTOR_TOKEN env var is required. Pull it from Secrets Manager:\n"
        "  export EXECUTOR_TOKEN=$(aws --profile aeo-admin secretsmanager "
        "get-secret-value --secret-id aeo-admin/prod --query SecretString "
        "--output text | python3 -c \"import json,sys;print(json.loads("
        "sys.stdin.read())['EXECUTOR_TOKEN'])\")"
    )
HEADERS = {
    "X-Executor-Token": TOKEN,
    "Content-Type": "application/json",
}
CSV_PATH = "audit_results/audit_log.csv"
SKIP_CLIENT_IDS = {"0"}
RUN_ID = 6
TODAY = "2026-04-17"


def _norm(s): return (s or "").strip().lower()


def _parse_rank(v):
    v = (v or "").strip()
    return int(v) if v.isdigit() and 1 <= int(v) <= 100 else None


def _normalize_iso_z(ts):
    """Convert CSV timestamp to ISO-8601 with Z suffix.

    Sending body.timestamp / body.date / body.createdAt makes the admin
    upsert key target the run date instead of "today UTC", preventing
    duplicate (kw, plat, date) rows on UTC-midnight crossover.
    """
    ts = (ts or "").strip()
    if not ts:
        return None
    if " " in ts and "T" not in ts:
        ts = ts.replace(" ", "T", 1)
    if not (ts.endswith("Z") or "+" in ts[10:] or "-" in ts[10:]):
        ts = ts + "Z"
    return ts


def _load_reason(path):
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return None
    return text[:500] or None


def main():
    # Fetch admin state
    biz = requests.get(f"{BASE}/api/businesses", headers=HEADERS, timeout=30).json()
    kws = requests.get(f"{BASE}/api/keywords", headers=HEADERS, timeout=30).json()
    reps = requests.get(f"{BASE}/api/ranking-reports", headers=HEADERS, timeout=30).json()

    biz_by_name = {_norm(b.get("name")): b for b in biz}
    kw_by_triple = {}
    for k in kws:
        key = (k.get("clientId"), k.get("businessId"), _norm(k.get("keywordText")))
        if None not in (k.get("clientId"), k.get("id")):
            kw_by_triple[key] = k["id"]

    # Set of (clientId, keywordId, platform, date) already in admin.
    # Including `date` is critical: without it, this script skips real
    # day-2 rows just because day-1 had a row for the same (kw, plat).
    already = set()
    for rep in reps:
        already.add((
            rep.get("clientId"),
            rep.get("keywordId"),
            (rep.get("platform") or "").lower(),
            rep.get("date"),
        ))
    print(f"Admin has {len(reps)} existing reports ({len(already)} unique cid/kid/plat/date)")

    # Build payloads from CSV, skip any that already exist
    with open(CSV_PATH, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["client_id"] not in SKIP_CLIENT_IDS]

    payloads = []
    skipped_existing = 0
    missing_map = []
    for r in rows:
        biz_name = _norm(r["biz_name"])
        b = biz_by_name.get(biz_name)
        if not b:
            missing_map.append(f"biz:{r['biz_name']}")
            continue
        cid, bid = b["clientId"], b["id"]
        kw_text = _norm(r["keyword"])
        kid = kw_by_triple.get((cid, bid, kw_text))
        if kid is None:
            # Fallback by (cid, kw_text) only
            cands = [k for k in kws if k.get("clientId") == cid and _norm(k.get("keywordText")) == kw_text]
            if len(cands) == 1:
                kid = cands[0]["id"]
        if kid is None:
            missing_map.append(f"kw:{cid}/{r['keyword']}")
            continue

        plat = (r["platform"] or "").lower()
        if plat not in {"chatgpt", "gemini", "perplexity"}:
            continue

        ts_iso = _normalize_iso_z(r.get("timestamp", ""))
        date_str = ts_iso[:10] if ts_iso else None

        if (cid, kid, plat, date_str) in already:
            skipped_existing += 1
            continue

        payloads.append({
            "clientId":         cid,
            "businessId":       bid,
            "keywordId":        kid,
            "platform":         plat,
            "rankingPosition":  _parse_rank(r.get("rank_position", "")),
            "reasonRecommended": _load_reason(r.get("response_text", "")),
            "isInitialRanking": False,
            "timestamp":        ts_iso,
            "date":             date_str,
            "createdAt":        ts_iso,
        })

    print(f"CSV rows (non-test): {len(rows)}")
    print(f"Already in admin:    {skipped_existing}")
    print(f"To push now:         {len(payloads)}")
    if missing_map:
        print(f"Unmappable:          {len(missing_map)} (first 5: {missing_map[:5]})")

    if not payloads:
        # Still patch the run as completed
        r = requests.patch(f"{BASE}/api/ranking-runs/{RUN_ID}", headers=HEADERS, json={
            "status": "completed",
            "finishedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "keywordsAttempted": 379,
            "keywordsSucceeded": 379 - skipped_existing - len(missing_map),
            "keywordsFailed": 0,
            "notes": f"Batch 2 audit push — resumed; all {skipped_existing} already in admin, completing run",
        }, timeout=30)
        print(f"PATCH run {RUN_ID}: HTTP {r.status_code}")
        return 0

    ok = fail = 0
    errs = []
    for i, p in enumerate(payloads, 1):
        r = requests.post(f"{BASE}/api/ranking-reports", headers=HEADERS, json=p, timeout=30)
        if r.status_code in (200, 201):
            ok += 1
        else:
            fail += 1
            errs.append(f"[{i}] cid={p['clientId']} kid={p['keywordId']} plat={p['platform']} → HTTP {r.status_code}: {r.text[:150]}")
        if i % 20 == 0 or i == len(payloads):
            print(f"  Progress: {i}/{len(payloads)}  ok={ok}  fail={fail}")
        time.sleep(0.05)

    # Finish run
    total_succeeded = skipped_existing + ok
    r = requests.patch(f"{BASE}/api/ranking-runs/{RUN_ID}", headers=HEADERS, json={
        "status": "completed",
        "finishedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "keywordsAttempted": 379,
        "keywordsSucceeded": total_succeeded,
        "keywordsFailed": fail,
        "notes": f"Resumed push — pushed {ok} new + {skipped_existing} already present; {fail} failed",
    }, timeout=30)
    print(f"\nPATCH run {RUN_ID}: HTTP {r.status_code}")
    print(f"Done. new={ok} already={skipped_existing} fail={fail} total={total_succeeded}/379")

    if errs:
        print("\nFirst 10 errors:")
        for e in errs[:10]:
            print(f"  {e}")

    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
