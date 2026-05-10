"""Push audit_log.csv → admin POST /api/audit-logs.

Usage:
  python3 push_audit_logs.py                    # dry-run (validates + prints sample)
  python3 push_audit_logs.py --push             # POST all rows

Resolution strategy (the CSV client_id is STALE — ignore it):
  campaign_id (CSV)  →  plan (admin)  →  clientId + businessId
  clientId + keyword →  keyword       →  keywordId

Does NOT bump keyword counters (audit runs don't count toward daily quota).
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from collections import Counter, defaultdict
from typing import Any

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
HEADERS = {"X-Executor-Token": TOKEN, "Content-Type": "application/json"}
DEFAULT_CSV = "audit_results/audit_log.csv"


def _opt_int(v: str | None) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _opt_float(v: str | None) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _opt_str(v: str | None) -> str | None:
    return v if v not in (None, "") else None


def _truncate(v: str | None, n: int) -> str | None:
    s = _opt_str(v)
    return s[:n] if s else None


def fetch_admin():
    plans = requests.get(f"{BASE}/api/aeo-plans", headers=HEADERS, timeout=30).json()
    kws = requests.get(f"{BASE}/api/keywords",  headers=HEADERS, timeout=30).json()
    plan_by_id = {p["id"]: p for p in plans}
    kw_by_cid_text: dict[tuple[int, str], int] = {}
    for k in kws:
        cid = k.get("clientId")
        t = (k.get("keywordText") or "").strip().lower()
        if cid and t:
            kw_by_cid_text[(cid, t)] = k["id"]
    return plan_by_id, kw_by_cid_text


def row_to_payload(r: dict[str, str],
                   plan_by_id: dict[int, dict],
                   kw_lookup: dict[tuple[int, str], int]) -> tuple[dict[str, Any] | None, str]:
    """Build a POST body for one CSV row, or (None, reason) if unresolvable."""
    caid = _opt_int(r.get("campaign_id"))
    plan = plan_by_id.get(caid) if caid else None
    if not plan:
        return None, f"campaign_id={caid} not in admin"

    real_cid = plan["clientId"]
    real_bid = plan.get("businessId")
    kw_text = (r.get("keyword") or "").strip()
    kw_id = kw_lookup.get((real_cid, kw_text.lower()))
    if not kw_id:
        return None, f"keyword {kw_text!r} not found for clientId={real_cid}"

    payload: dict[str, Any] = {
        "clientId":   real_cid,
        "businessId": real_bid,
        "campaignId": caid,
        "keywordId":  kw_id,
        # Snapshot strings (kept even if FKs rename/delete later)
        "bizName":      _opt_str(r.get("biz_name")),
        "campaignName": _opt_str(r.get("campaign_name")),
        "keywordText":  _opt_str(kw_text),
        # Run details
        "platform":        (_opt_str(r.get("platform")) or "").lower() or None,
        "mode":            _opt_str(r.get("mode")) or "adb",
        "device":          _truncate(r.get("device"), 40),
        "status":          _opt_str(r.get("status")) or "success",
        "durationSeconds": _opt_float(r.get("duration_s")),
        # Ranking
        "rankPosition": _opt_int(r.get("rank_position")),
        "rankTotal":    _opt_int(r.get("rank_total")),
        "mentioned":    _opt_str(r.get("mentioned")),
        "rankContext":  _opt_str(r.get("rank_context")),
        # Artifacts
        "screenshotPath": _opt_str(r.get("screenshot")),
        "responseText":   _opt_str(r.get("response_text")),
        "prompt":         _opt_str(r.get("prompt")),
        "error":          _opt_str(r.get("error")),
        # Proxy
        "proxyUsername": _opt_str(r.get("proxy_username")),
        "proxyIp":       _opt_str(r.get("proxy_ip")),
        "proxyCity":     _opt_str(r.get("proxy_city")),
        "proxyRegion":   _opt_str(r.get("proxy_region")),
        "proxyZip":      _opt_str(r.get("proxy_zip")),
    }
    return payload, ""


def validate(payloads: list[dict[str, Any]]) -> list[str]:
    """Quick sanity check — return list of warnings."""
    warnings: list[str] = []

    # Required fields
    for i, p in enumerate(payloads):
        for fld in ("clientId", "campaignId", "keywordId", "platform", "status"):
            if p.get(fld) is None:
                warnings.append(f"row {i}: missing {fld}")
                break

    # Platform enum (admin stores all platform values lowercase)
    allowed_plat = {"gemini", "chatgpt", "perplexity"}
    bad_plat = [i for i, p in enumerate(payloads) if p.get("platform") not in allowed_plat]
    if bad_plat:
        warnings.append(f"{len(bad_plat)} rows have unexpected platform")

    # Status enum
    allowed_status = {"success", "error"}
    bad_status = [i for i, p in enumerate(payloads) if p.get("status") not in allowed_status]
    if bad_status:
        warnings.append(f"{len(bad_status)} rows have unexpected status")

    # error required when status=error
    bad_err = [i for i, p in enumerate(payloads) if p.get("status") == "error" and not p.get("error")]
    if bad_err:
        warnings.append(f"{len(bad_err)} error rows missing 'error' message")

    return warnings


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--push"]
    do_push = "--push" in sys.argv
    csv_path = args[0] if args else DEFAULT_CSV

    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    print(f"Loaded {len(rows)} rows from {csv_path}")

    print("Fetching admin plans + keywords...")
    plan_by_id, kw_lookup = fetch_admin()
    print(f"  {len(plan_by_id)} plans, {len(kw_lookup)} keyword lookups")

    payloads: list[dict[str, Any]] = []
    skipped: list[str] = []
    for r in rows:
        p, reason = row_to_payload(r, plan_by_id, kw_lookup)
        if p is None:
            skipped.append(reason)
        else:
            payloads.append(p)

    print(f"\nResolved: {len(payloads)}/{len(rows)}  |  Skipped: {len(skipped)}")
    if skipped:
        for reason, n in Counter(skipped).most_common(5):
            print(f"  {n}×  {reason}")

    warnings = validate(payloads)
    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for w in warnings[:10]:
            print(f"  {w}")
    else:
        print("\n✓ All payloads pass validation")

    # Per-client summary
    by_client: dict[int, int] = defaultdict(int)
    for p in payloads:
        by_client[p["clientId"]] += 1
    print(f"\nPer-client distribution:")
    for cid, n in sorted(by_client.items()):
        sample = next(p for p in payloads if p["clientId"] == cid)
        print(f"  clientId={cid:3d}  {sample['bizName'][:40]:<40}  {n} rows")

    if not do_push:
        print(f"\n=== DRY RUN — first payload ===")
        print(json.dumps(payloads[0], indent=2))
        print(f"\n{len(payloads)} rows ready. Run with --push to POST.")
        return 0

    # ── POST ──
    print(f"\n— Pushing {len(payloads)} rows to {BASE}/api/audit-logs —")
    ok = fail = 0
    errors: list[str] = []
    for i, p in enumerate(payloads, 1):
        r = requests.post(f"{BASE}/api/audit-logs", headers=HEADERS, json=p, timeout=30)
        if r.status_code in (200, 201):
            ok += 1
        else:
            fail += 1
            errors.append(f"[{i}] cid={p['clientId']} cam={p['campaignId']} plat={p['platform']} → HTTP {r.status_code}: {r.text[:180]}")
        if i % 50 == 0 or i == len(payloads):
            print(f"  progress: {i}/{len(payloads)}  ok={ok}  fail={fail}")
        time.sleep(0.03)

    os.makedirs("audit_logs", exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    summary = f"audit_logs/audit_push_{ts}.json"
    with open(summary, "w") as f:
        json.dump({
            "csv":     csv_path,
            "loaded":  len(rows),
            "pushed":  ok,
            "failed":  fail,
            "skipped": skipped,
            "errors":  errors[:30],
        }, f, indent=2)
    print(f"\nSummary: {summary}")
    print(f"POST: ok={ok}/{len(payloads)}  fail={fail}")
    if errors:
        for e in errors[:5]:
            print(f"  {e}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
