"""Delete today's sessions for a client and reset their keyword counters.

Usage: python3 cleanup_client.py <clientId> [--dry]
"""
from __future__ import annotations
import sys, requests, json
from datetime import datetime, timezone, timedelta

ADMIN = "https://jjm59vpn3y.us-east-1.awsapprunner.com"
TOKEN = "89d0385d06ab80d79a034745c978a298f4728c85066616f359cb8b9bb87e5644"
H = {"X-Executor-Token": TOKEN, "Content-Type": "application/json"}


def main(client_id: int, dry: bool) -> int:
    print(f"Cleanup for clientId={client_id} dry={dry}")

    # Fetch this client's sessions
    r = requests.get(f"{ADMIN}/api/sessions?clientId={client_id}&limit=200", headers=H, timeout=30).json()
    sessions = r.get("sessions", [])
    print(f"Found {len(sessions)} existing sessions for client {client_id}")

    # Count keyword bumps that came from these sessions
    from collections import Counter
    kw_hits = Counter()
    kw_fu_hits = Counter()
    for s in sessions:
        if s.get("keywordId"):
            kw_hits[s["keywordId"]] += 1
            if s.get("followupText"):
                kw_fu_hits[s["keywordId"]] += 1

    print(f"\nKeyword hits to reverse:")
    for kid, n in kw_hits.items():
        fu = kw_fu_hits.get(kid, 0)
        print(f"  kid={kid}: initial -{n}  followup -{fu}")

    if dry:
        print("\nDRY RUN — no changes")
        return 0

    # Delete sessions
    deleted = 0
    fail = 0
    for s in sessions:
        resp = requests.delete(f"{ADMIN}/api/sessions/{s['id']}", headers=H, timeout=20)
        if resp.status_code in (200, 204):
            deleted += 1
        else:
            fail += 1
            print(f"  DELETE session {s['id']} → {resp.status_code}: {resp.text[:100]}")
    print(f"\nDeleted {deleted}/{len(sessions)} sessions  ({fail} failed)")

    # Reverse keyword counters
    kws = requests.get(f"{ADMIN}/api/keywords?clientId={client_id}", headers=H, timeout=30).json()
    kw_by_id = {k["id"]: k for k in kws}
    patched = 0
    for kid, n in kw_hits.items():
        cur = kw_by_id.get(kid)
        if not cur:
            continue
        body = {
            "initialSearchCount30Days": max(0, (cur.get("initialSearchCount30Days") or 0) - n),
            "initialSearchCountLife":   max(0, (cur.get("initialSearchCountLife") or 0) - n),
        }
        fu = kw_fu_hits.get(kid, 0)
        if fu:
            body["followupSearchCount30Days"] = max(0, (cur.get("followupSearchCount30Days") or 0) - fu)
            body["followupSearchCountLife"]   = max(0, (cur.get("followupSearchCountLife") or 0) - fu)
        resp = requests.patch(f"{ADMIN}/api/keywords/{kid}", headers=H, json=body, timeout=20)
        if resp.status_code in (200, 201):
            patched += 1
        else:
            print(f"  PATCH kw {kid} → {resp.status_code}: {resp.text[:100]}")
    print(f"Reset {patched}/{len(kw_hits)} keyword counters")

    return 0 if fail == 0 else 2


if __name__ == "__main__":
    args = sys.argv[1:]
    dry = "--dry" in args
    args = [a for a in args if not a.startswith("--")]
    if not args:
        print("usage: cleanup_client.py <clientId> [--dry]")
        sys.exit(2)
    sys.exit(main(int(args[0]), dry))
