"""Push 60 sessions from sessions_log.final.csv to admin BE, then bump keyword counts.

Flow:
  1. Fetch admin businesses + keywords → build lookups
  2. For each CSV row → POST /api/sessions
  3. Group sessions by keywordId → for each keyword, PATCH counts:
       - initialSearchCount += N sessions
       - initialSearchCountLife += N
       - followupSearchCount += M sessions where follow_up was sent
       - followupSearchCountLife += M
  4. Report summary
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from collections import defaultdict, Counter

import requests


BASE = "https://jjm59vpn3y.us-east-1.awsapprunner.com"
TOKEN = "89d0385d06ab80d79a034745c978a298f4728c85066616f359cb8b9bb87e5644"
HEADERS = {
    "X-Executor-Token": TOKEN,
    "Content-Type": "application/json",
}
CSV_PATH_DEFAULT = "sessions_log.final.csv"


def _norm(s): return (s or "").strip().lower()


def extract_session_id(username: str) -> str | None:
    """user-xxx-session-<SID>-sessionduration-..."""
    if not username or "session-" not in username:
        return None
    try:
        tail = username.split("session-", 1)[1]
        return tail.split("-", 1)[0]
    except Exception:
        return None


def fetch_admin():
    biz = requests.get(f"{BASE}/api/businesses", headers=HEADERS, timeout=30).json()
    kws = requests.get(f"{BASE}/api/keywords", headers=HEADERS, timeout=30).json()
    biz_by_name = {_norm(b.get("name")): b for b in biz}
    kw_by_triple: dict[tuple[int, int | None, str], dict] = {}
    for k in kws:
        key = (k.get("clientId"), k.get("businessId"), _norm(k.get("keywordText")))
        if k.get("id") is not None and k.get("clientId") is not None:
            kw_by_triple[key] = k
    return biz, biz_by_name, kws, kw_by_triple


def post_session(payload: dict) -> tuple[bool, str, dict | None]:
    r = requests.post(f"{BASE}/api/sessions", headers=HEADERS, json=payload, timeout=30)
    if r.status_code in (200, 201):
        return True, "", r.json() if r.text else None
    return False, f"HTTP {r.status_code}: {r.text[:200]}", None


def patch_keyword(kid: int, body: dict) -> tuple[bool, str]:
    r = requests.patch(f"{BASE}/api/keywords/{kid}", headers=HEADERS, json=body, timeout=30)
    if r.status_code in (200, 201):
        return True, ""
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else CSV_PATH_DEFAULT
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    print(f"Loaded {len(rows)} rows from {csv_path}")

    biz, biz_by_name, kws, kw_by_triple = fetch_admin()
    print(f"Admin: {len(biz)} businesses, {len(kws)} keywords")

    # Resolve admin IDs for each row
    payloads: list[tuple[dict, dict]] = []  # (raw_row, payload)
    unmapped: list[str] = []
    for r in rows:
        biz_name_norm = _norm(r["biz_name"])
        b = biz_by_name.get(biz_name_norm)
        # Fallback by prefix match (admin may have ", City" suffix)
        if not b:
            for k, v in biz_by_name.items():
                if k.startswith(biz_name_norm) or biz_name_norm in k:
                    if _norm(r["city"]) in _norm(v.get("name")):
                        b = v
                        break
        if not b:
            unmapped.append(f"biz not found: {r['biz_name']}/{r['city']}")
            continue

        cid = b["clientId"]
        bid = b["id"]
        kw_text = _norm(r["keyword"])
        kw_admin = kw_by_triple.get((cid, bid, kw_text))
        if not kw_admin:
            # Fallback: match by clientId + text only
            cands = [k for k in kws if k.get("clientId") == cid and _norm(k.get("keywordText")) == kw_text]
            if len(cands) == 1:
                kw_admin = cands[0]
        if not kw_admin:
            unmapped.append(f"kw not found: cid={cid} bid={bid} kw={r['keyword']}")
            continue

        plat = (r["platform"] or "").lower()
        session_id = extract_session_id(r["proxy_username"])
        payload = {
            "clientId":       cid,
            "businessId":     bid,
            "keywordId":      kw_admin["id"],
            "deviceId":       None,
            "proxyId":        None,
            "promptText":     r.get("prompt") or None,
            "followupText":   (r.get("follow_up") or None) if r.get("has_follow_up") == "True" else None,
            "aiPlatform":     plat,
            "status":         r.get("status", "success"),
            "type":           "aeo",
            "proxyUsername":  r.get("proxy_username") or None,
            "proxySessionId": session_id,
        }
        payloads.append((r, payload))

    print(f"Payloads ready: {len(payloads)}  |  unmapped: {len(unmapped)}")
    if unmapped:
        print("First 5 unmapped:")
        for u in unmapped[:5]:
            print(f"  {u}")

    if not payloads:
        print("Nothing to push.")
        return 1

    # Step 1: POST sessions
    print(f"\n— Pushing {len(payloads)} sessions —")
    session_ok = 0
    session_fail = 0
    errors: list[str] = []
    per_keyword_stats: dict[int, dict] = defaultdict(lambda: {"initial": 0, "followup": 0})
    for i, (r, p) in enumerate(payloads, 1):
        ok, err, created = post_session(p)
        if ok:
            session_ok += 1
            per_keyword_stats[p["keywordId"]]["initial"] += 1
            if p["followupText"]:
                per_keyword_stats[p["keywordId"]]["followup"] += 1
        else:
            session_fail += 1
            errors.append(f"[{i}] kid={p['keywordId']} plat={p['aiPlatform']} → {err}")
        if i % 15 == 0 or i == len(payloads):
            print(f"  Progress: {i}/{len(payloads)}  ok={session_ok}  fail={session_fail}")
        time.sleep(0.05)

    # Step 2: PATCH keyword counts
    print(f"\n— Patching {len(per_keyword_stats)} keyword counters —")
    patch_ok = 0
    patch_fail = 0
    patch_errors: list[str] = []
    # Need current values
    kw_by_id = {k["id"]: k for k in kws}
    for kid, bumps in per_keyword_stats.items():
        cur = kw_by_id.get(kid, {})
        cur_init_30  = int(cur.get("initialSearchCount30Days") or 0)
        cur_init_life = int(cur.get("initialSearchCountLife") or 0)
        cur_fu_30   = int(cur.get("followupSearchCount30Days") or 0)
        cur_fu_life = int(cur.get("followupSearchCountLife") or 0)
        body = {
            "initialSearchCount30Days": cur_init_30 + bumps["initial"],
            "initialSearchCountLife":   cur_init_life + bumps["initial"],
        }
        if bumps["followup"]:
            body["followupSearchCount30Days"] = cur_fu_30 + bumps["followup"]
            body["followupSearchCountLife"]   = cur_fu_life + bumps["followup"]
        ok, err = patch_keyword(kid, body)
        if ok:
            patch_ok += 1
        else:
            patch_fail += 1
            patch_errors.append(f"kid={kid} bumps={bumps} → {err}")
        time.sleep(0.05)
    print(f"  Keyword patches: ok={patch_ok}  fail={patch_fail}")

    os.makedirs("audit_logs", exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    summary_path = f"audit_logs/session_push_{ts}.json"
    with open(summary_path, "w") as f:
        json.dump({
            "session_total":  len(payloads),
            "session_ok":     session_ok,
            "session_fail":   session_fail,
            "keyword_patches": dict(per_keyword_stats),
            "patch_ok":       patch_ok,
            "patch_fail":     patch_fail,
            "errors":         errors[:30],
            "patch_errors":   patch_errors[:30],
            "unmapped":       unmapped,
        }, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Sessions: ok={session_ok}/{len(payloads)}  fail={session_fail}")
    print(f"Keyword counts patched: ok={patch_ok}/{len(per_keyword_stats)}  fail={patch_fail}")
    print(f"Summary: {summary_path}")
    if errors:
        print("\nFirst 5 session errors:")
        for e in errors[:5]: print(f"  {e}")
    if patch_errors:
        print("\nFirst 5 patch errors:")
        for e in patch_errors[:5]: print(f"  {e}")

    return 0 if (session_fail == 0 and patch_fail == 0) else 2


if __name__ == "__main__":
    sys.exit(main())
