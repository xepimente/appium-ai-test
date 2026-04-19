"""One-shot backfill: add the rendered `prompt` column to existing
audit_results/audit_log.csv rows.

The audit prompt template is fixed (see audit.AUDIT_PROMPT_TEMPLATE), so we
can recompute each row's prompt deterministically from:
  - keyword        (already in the CSV)
  - biz_name       (from clients.json backups; fallback: strip ", <city>" from CSV biz_name)
  - city / state   (from clients.json backups; fallback: parse from CSV campaign_name address)
  - biz_url        (from clients.json backups; fallback: empty string)

Why: we're about to push audit rows to the admin and they want the literal
prompt the model saw. Backward-compat only — going forward, audit.py writes
the prompt column at log time.

Usage:
  python3 backfill_audit_prompts.py              # write in place (with backup)
  python3 backfill_audit_prompts.py --dry        # preview only, no write
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional


from audit import AUDIT_PROMPT_TEMPLATE


AUDIT_LOG     = "audit_results/audit_log.csv"
BACKUP_PATH   = "audit_results/audit_log.csv.bak.before_prompt_backfill"

# Ordered: earlier sources win only if the later one lacks the id. In practice
# we merge — every source contributes any new client_ids it has.
CLIENT_SOURCES = [
    "clients_full_25.json",
    "clients.json.bak.daily20",
    "clients.json.bak.20260417",
    "clients_daily.json",
    "clients.json",
]


@dataclass
class ClientInfo:
    biz_name: str = ""
    biz_url:  str = ""
    city:     str = ""
    state:    str = ""


def _load_clients_index() -> Dict[int, ClientInfo]:
    """Merge every clients.json backup into {client_id → ClientInfo}.

    First source to define a client_id wins (files are ordered most→least
    complete)."""
    idx: Dict[int, ClientInfo] = {}
    for path in CLIENT_SOURCES:
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as e:
            print(f"  [skip] {path}: {e}", file=sys.stderr)
            continue
        if isinstance(data, dict):
            data = list(data.values())
        for c in data:
            cid = c.get("client_id") or c.get("id")
            if cid is None or cid in idx:
                continue
            idx[cid] = ClientInfo(
                biz_name = c.get("biz_name") or c.get("name") or "",
                biz_url  = c.get("biz_url") or "",
                city     = c.get("city") or "",
                state    = c.get("state") or "",
            )
    return idx


_ADDR_RX = re.compile(r",\s*([A-Za-z][A-Za-z \-']+?),\s*([A-Z]{2})\s+\d{5}", re.ASCII)


def _derive_from_row(row: Dict[str, str]) -> ClientInfo:
    """Fallback when a client_id isn't in any clients.json backup.

    CSV fields we can mine:
      biz_name        "West Valley Naturopathic Center, Goodyear"   → strip ", Goodyear"
      campaign_name   "...— 1646 N Litchfield Rd #200, Goodyear, AZ 85395, USA"
                      → extract ("Goodyear", "AZ")
    biz_url is unknowable from the CSV alone — default to empty string.
    """
    csv_biz = (row.get("biz_name") or "").strip()
    campaign = (row.get("campaign_name") or "").strip()

    city = state = ""
    m = _ADDR_RX.search(campaign)
    if m:
        city, state = m.group(1).strip(), m.group(2).strip()

    biz_name = csv_biz
    if city and biz_name.lower().endswith(f", {city.lower()}"):
        biz_name = biz_name[: -(len(city) + 2)].strip()

    return ClientInfo(biz_name=biz_name, biz_url="", city=city, state=state)


def _render_prompt(info: ClientInfo, keyword: str) -> str:
    """Feed ClientInfo + keyword through the fixed audit template."""
    return AUDIT_PROMPT_TEMPLATE.format(
        keyword  = keyword,
        city     = info.city,
        state    = info.state,
        biz_name = info.biz_name,
        biz_url  = info.biz_url,
    )


def backfill(dry: bool) -> int:
    if not os.path.exists(AUDIT_LOG):
        print(f"{AUDIT_LOG} not found", file=sys.stderr)
        return 1

    with open(AUDIT_LOG, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if "prompt" not in fieldnames:
        fieldnames = fieldnames + ["prompt"]

    client_idx = _load_clients_index()
    print(f"Loaded {len(client_idx)} clients from {len([p for p in CLIENT_SOURCES if os.path.exists(p)])} sources")
    print(f"Found {len(rows)} rows in {AUDIT_LOG}\n")

    src_hit = src_csv = 0
    missing_ids: List[int] = []
    for row in rows:
        try:
            cid = int(row.get("client_id") or 0)
        except ValueError:
            cid = 0
        info = client_idx.get(cid)
        if info is None:
            info = _derive_from_row(row)
            missing_ids.append(cid)
            src_csv += 1
        else:
            src_hit += 1
        row["prompt"] = _render_prompt(info, row.get("keyword", ""))

    print(f"Prompt resolved from clients.json: {src_hit}")
    print(f"Prompt derived from CSV fallback:   {src_csv}")
    if missing_ids:
        unique_missing = sorted(set(missing_ids))
        print(f"Client IDs not in any backup (used CSV fallback): {unique_missing}")

    if rows:
        print("\nSample (first row):")
        print(f"  cid={rows[0]['client_id']}  keyword={rows[0]['keyword']!r}")
        print(f"  prompt[:140]={rows[0]['prompt'][:140]!r}")

    if dry:
        print("\nDRY RUN — no write")
        return 0

    if not os.path.exists(BACKUP_PATH):
        shutil.copy(AUDIT_LOG, BACKUP_PATH)
        print(f"\nBackup: {BACKUP_PATH}")

    with open(AUDIT_LOG, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows → {AUDIT_LOG}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="Preview only, no write")
    args = ap.parse_args()
    sys.exit(backfill(dry=args.dry))
