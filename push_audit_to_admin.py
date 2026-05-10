"""Push batch 2 audit results to admin BE.

Mapping strategy:
  1. CSV biz_name → admin business by exact name match → businessId + clientId
  2. (admin clientId + admin businessId + keywordText) → admin keywordId
  3. POST /api/ranking-reports with admin IDs

Skip rows that can't be mapped (report them in summary). Skip client_id=0 test data.
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


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _parse_rank(v: str) -> int | None:
    v = (v or "").strip()
    if v.isdigit():
        n = int(v)
        return n if 1 <= n <= 100 else None
    return None


def _normalize_iso_z(ts: str) -> str | None:
    """Convert CSV timestamp to ISO-8601 with Z suffix.

    CSVs come as "2026-05-07 13:57:47" or "2026-05-07T13:57:47Z". The admin
    API parses ISO-Z strings (avoids pg-node Date-object TZ leak). Sending
    `date` and `createdAt` makes the upsert key target the run date, not
    "today UTC" — preventing duplicate (kw, plat, date) rows on UTC-midnight
    crossover.
    """
    ts = (ts or "").strip()
    if not ts:
        return None
    if " " in ts and "T" not in ts:
        ts = ts.replace(" ", "T", 1)
    if not (ts.endswith("Z") or "+" in ts[10:] or "-" in ts[10:]):
        ts = ts + "Z"
    return ts


def _load_reason(path: str) -> str | None:
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


def fetch_all() -> tuple[list[dict], list[dict]]:
    biz = requests.get(f"{BASE}/api/businesses", headers=HEADERS, timeout=30).json()
    kws = requests.get(f"{BASE}/api/keywords", headers=HEADERS, timeout=30).json()
    biz = biz if isinstance(biz, list) else biz.get("data") or []
    kws = kws if isinstance(kws, list) else kws.get("data") or []
    print(f"Admin: {len(biz)} businesses, {len(kws)} keywords")
    return biz, kws


def build_biz_index(biz: list[dict]) -> dict[str, dict]:
    idx = {}
    for b in biz:
        name = _norm(b.get("name"))
        if name:
            idx[name] = b
    return idx


def build_keyword_index(kws: list[dict]) -> dict[tuple[int, int | None, str], int]:
    """Key: (clientId, businessId, keywordTextLower) -> keywordId."""
    idx: dict[tuple[int, int | None, str], int] = {}
    for k in kws:
        cid = k.get("clientId")
        bid = k.get("businessId")
        kid = k.get("id")
        text = _norm(k.get("keywordText"))
        if cid is None or kid is None or not text:
            continue
        idx[(cid, bid, text)] = kid
    return idx


def find_keyword_id(kw_idx: dict[tuple[int, int | None, str], int],
                    kws: list[dict],
                    clientId: int,
                    businessId: int | None,
                    keywordText: str) -> int | None:
    key = (clientId, businessId, _norm(keywordText))
    if key in kw_idx:
        return kw_idx[key]
    # Fallback: match by (clientId, text) only
    text_norm = _norm(keywordText)
    candidates = [k for k in kws
                  if k.get("clientId") == clientId and _norm(k.get("keywordText")) == text_norm]
    if len(candidates) == 1:
        return candidates[0]["id"]
    # Fallback: match by text only across whole admin
    candidates = [k for k in kws if _norm(k.get("keywordText")) == text_norm]
    if len(candidates) == 1:
        return candidates[0]["id"]
    return None


def create_run(attempted: int, notes: str) -> int:
    body = {
        "status": "running",
        "keywordsAttempted": attempted,
        "keywordsSucceeded": 0,
        "keywordsFailed": 0,
        "notes": notes,
    }
    r = requests.post(f"{BASE}/api/ranking-runs", headers=HEADERS, json=body, timeout=30)
    r.raise_for_status()
    run = r.json()
    rid = run.get("id") or (run.get("data") or {}).get("id")
    print(f"Created ranking run id={rid}")
    return rid


def finish_run(run_id: int, attempted: int, succeeded: int, failed: int, notes: str) -> None:
    body = {
        "status": "completed",
        "finishedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "keywordsAttempted": attempted,
        "keywordsSucceeded": succeeded,
        "keywordsFailed": failed,
        "notes": notes,
    }
    r = requests.patch(f"{BASE}/api/ranking-runs/{run_id}", headers=HEADERS, json=body, timeout=30)
    r.raise_for_status()


def post_report(payload: dict) -> tuple[bool, str]:
    r = requests.post(f"{BASE}/api/ranking-reports", headers=HEADERS, json=payload, timeout=30)
    if r.status_code in (200, 201):
        return True, ""
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


def main(dry_run: bool = False) -> int:
    with open(CSV_PATH, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["client_id"] not in SKIP_CLIENT_IDS]
    print(f"Loaded {len(rows)} rows from {CSV_PATH} (excluding client 0)")

    biz, kws = fetch_all()
    biz_by_name = build_biz_index(biz)
    kw_idx = build_keyword_index(kws)

    payloads: list[dict] = []
    missing_biz: dict[str, int] = {}
    missing_kw: list[str] = []
    bad_plat = 0

    for r in rows:
        biz_name = r["biz_name"].strip()
        keyword = r["keyword"].strip()
        plat = r["platform"].strip().lower()

        if plat not in {"chatgpt", "gemini", "perplexity"}:
            bad_plat += 1
            continue

        b = biz_by_name.get(_norm(biz_name))
        if not b:
            missing_biz[biz_name] = missing_biz.get(biz_name, 0) + 1
            continue

        clientId = b.get("clientId")
        businessId = b.get("id")
        kid = find_keyword_id(kw_idx, kws, clientId, businessId, keyword)
        if not kid:
            missing_kw.append(f"biz={biz_name!r} (admin bid={businessId} cid={clientId}) kw={keyword!r}")
            continue

        ts_iso = _normalize_iso_z(r.get("timestamp", ""))
        date_str = ts_iso[:10] if ts_iso else None

        payloads.append({
            "clientId": clientId,
            "businessId": businessId,
            "keywordId": kid,
            "platform": plat,
            "rankingPosition": _parse_rank(r.get("rank_position", "")),
            "rankingTotal": r.get("ranking_total", ""),
            "reasonRecommended": _load_reason(r.get("response_text", "")),
            "isInitialRanking": False,
            "timestamp": ts_iso,
            "date": date_str,
            "createdAt": ts_iso,
            # new fields from audit runner
            "clientName": r.get("client_name", ""),
            "bizName": r.get("biz_name", ""),
            "searchAddress": r.get("search_address", ""),
            "keyword": r.get("keyword", ""),
            "status": "success" if _parse_rank(r.get("rank_position", "")) else "error",
            "durationSeconds": r.get("duration_s", ""),
            "deviceIdentifier": r.get("device_id", ""),
            "proxyStatus": r.get("proxy_status", ""),
            "proxyHost": r.get("proxy_host", ""),
            "proxyPort": r.get("proxy_port", ""),
            "proxyUsername": r.get("proxy_username", ""),
            "baseLatitude": r.get("base_latitude", ""),
            "baseLongitude": r.get("base_longitude", ""),
            "mockedLatitude": r.get("mocked_latitude", ""),
            "mockedLongitude": r.get("mocked_longitude", ""),
            "mockedTimezone": r.get("mocked_timezone", ""),
            "screenshotUrl": r.get("screenshot_url", ""),
            "failureStep": r.get("failure_step", ""),
            "error": r.get("error", ""),
        })

    print(f"\nMappable payloads: {len(payloads)}")
    print(f"Skipped: bad_platform={bad_plat}  missing_biz={sum(missing_biz.values())}  missing_kw={len(missing_kw)}")
    if missing_biz:
        print("Missing biz_name matches (biz_name: row_count):")
        for name, n in sorted(missing_biz.items(), key=lambda x: -x[1])[:10]:
            print(f"  {name!r}: {n}")
    if missing_kw:
        print(f"Missing keyword matches (first 10 of {len(missing_kw)}):")
        for m in missing_kw[:10]:
            print(f"  {m}")

    if dry_run:
        print("\nDRY RUN — sample payload:")
        if payloads:
            print(json.dumps(payloads[0], indent=2))
        return 0

    if not payloads:
        print("Nothing to push.")
        return 1

    run_id = create_run(len(payloads),
                       f"Batch 2 audit push — {len(payloads)} reports from audit_log.csv")
    ok = fail = 0
    errors: list[str] = []
    for i, p in enumerate(payloads, 1):
        success, err = post_report(p)
        if success:
            ok += 1
        else:
            fail += 1
            errors.append(f"[row {i} cid={p['clientId']} kid={p['keywordId']} plat={p['platform']}] {err}")
        if i % 25 == 0 or i == len(payloads):
            print(f"  Progress: {i}/{len(payloads)}  ok={ok}  fail={fail}")
        time.sleep(0.05)

    finish_run(run_id, len(payloads), ok, fail,
               f"Pushed {ok} reports; {fail} failed; skipped {sum(missing_biz.values())} missing biz / {len(missing_kw)} missing kw")

    os.makedirs("audit_logs", exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    summary_path = f"audit_logs/push_summary_{ts}.json"
    with open(summary_path, "w") as f:
        json.dump({
            "runId": run_id, "total": len(payloads), "ok": ok, "fail": fail,
            "missing_biz": missing_biz, "missing_kw": missing_kw,
            "errors": errors[:50],
        }, f, indent=2)
    print(f"\nDone. runId={run_id} ok={ok} fail={fail}. Summary: {summary_path}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry" in sys.argv))
