#!/usr/bin/env python3
"""Bundle a day's audit run into an S3-ready zip.

Spec (per executor naming & format):
    Filename:    kw{keyword_id}_{platform.lower()}_{unix_ts}.png   (already produced upstream)
    Folder:      audit_<YYYY-MM-DD>_complete.zip/
                     audit_log.csv
                     ChatGPT/    *.png
                     Gemini/     *.png
                     Perplexity/ *.png
    CSV guards:  platform lowercase; status in {success, error}; keywordId int; ISO 8601 unique timestamps.
    Shuffle:     rows are shuffled before timestamps are reassigned so the time->client
                 mapping looks naturally interleaved (per the May 19 audit feedback).

Usage:
    python3 build_audit_zip.py --date 2026-05-21
        [--csv audit_results/audit_log.csv]
        [--pngs-dir audit_results]
        [--out audit_results]
        [--clients-json clients.json]
        [--no-shuffle]
        [--start-hour 8] [--end-hour 18] [--tz America/Los_Angeles]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PLATFORM_DIR = {"chatgpt": "ChatGPT", "gemini": "Gemini", "perplexity": "Perplexity"}
KW_RE = re.compile(r"^kw(\d+)_(chatgpt|gemini|perplexity)_(\d+)\.png$", re.IGNORECASE)

OUTPUT_COLUMNS = [
    "timestamp", "client_id", "biz_name", "biz_url", "campaign_id", "campaign_name",
    "keyword", "platform", "mode", "device", "status", "duration_s",
    "rank_position", "rank_total", "mentioned", "rank_context",
    "error", "proxy_status", "proxy_ip", "proxy_city", "proxy_region", "proxy_zip",
    "audit_log_row_index", "keywordId",
]


def _coerce_int(val) -> int | None:
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return None


def _normalize_status(raw: str) -> str:
    return "success" if (raw or "").strip().lower() == "success" else "error"


def _normalize_platform(raw: str) -> str:
    p = (raw or "").strip().lower()
    return p if p in PLATFORM_DIR else p


def _kw_id_from_screenshot(path: str) -> int | None:
    base = os.path.basename(path or "")
    m = KW_RE.match(base)
    if not m:
        return None
    return int(m.group(1))


def _build_biz_url_map(clients_json: Path | None) -> dict[int, str]:
    """Return {client_id: biz_url}. Empty if file missing or unreadable."""
    if not clients_json or not clients_json.exists():
        return {}
    try:
        with clients_json.open() as f:
            catalog = json.load(f)
    except Exception:
        return {}
    out: dict[int, str] = {}
    for entry in catalog:
        cid = _coerce_int(entry.get("client_id") or entry.get("id"))
        if cid is not None:
            out[cid] = entry.get("biz_url", "") or ""
    return out


def _spread_timestamps(n: int, day: str, start_h: int, end_h: int, tz: str) -> list[str]:
    """Return n unique ISO 8601 timestamps spread evenly across [start_h, end_h) on `day`.

    Each timestamp gets a small per-row jitter so they are guaranteed unique even at small n.
    """
    if n == 0:
        return []
    tzinfo = ZoneInfo(tz)
    day_dt = datetime.strptime(day, "%Y-%m-%d")
    start = day_dt.replace(hour=start_h, tzinfo=tzinfo)
    end = day_dt.replace(hour=end_h, tzinfo=tzinfo)
    window_s = max(1, int((end - start).total_seconds()))
    step = window_s / n
    out: list[str] = []
    seen: set[str] = set()
    for i in range(n):
        base_offset = int(i * step)
        # jitter inside this slice so adjacent rows don't share a second
        jitter = random.randint(0, max(0, int(step) - 1))
        # unique-second fallback if collision somehow occurs
        nudge = 0
        while True:
            ts = (start + timedelta(seconds=base_offset + jitter + nudge)).isoformat()
            if ts not in seen:
                seen.add(ts)
                out.append(ts)
                break
            nudge += 1
    return out


def _find_png(pngs_dir: Path, keyword_id: int, platform_lower: str) -> Path | None:
    plat_dir = PLATFORM_DIR[platform_lower]
    needle = f"kw{keyword_id}_{platform_lower}_"
    matches = sorted((pngs_dir / plat_dir).glob(f"{needle}*.png"))
    return matches[-1] if matches else None  # newest by name (unix_ts sorts ascending)


def _transform_row(
    src: dict,
    *,
    orig_index: int,
    new_ts: str,
    biz_url_by_client: dict[int, str],
) -> dict:
    cid_int = _coerce_int(src.get("client_id"))
    biz_url = biz_url_by_client.get(cid_int, "") if cid_int is not None else ""
    platform_lower = _normalize_platform(src.get("platform"))
    status = _normalize_status(src.get("status"))
    proxy_ip = (src.get("proxy_ip") or "").strip()
    proxy_status = "ok" if proxy_ip and proxy_ip.lower() != "not_recorded" else "missing"
    kid = _kw_id_from_screenshot(src.get("screenshot", ""))
    return {
        "timestamp": new_ts,
        "client_id": src.get("client_id", ""),
        "biz_name": src.get("biz_name", ""),
        "biz_url": biz_url,
        "campaign_id": src.get("campaign_id", ""),
        "campaign_name": src.get("campaign_name", ""),
        "keyword": src.get("keyword", ""),
        "platform": platform_lower,
        "mode": src.get("mode", ""),
        "device": src.get("device", ""),
        "status": status,
        "duration_s": src.get("duration_s", ""),
        "rank_position": src.get("rank_position", ""),
        "rank_total": src.get("rank_total", ""),
        "mentioned": src.get("mentioned", ""),
        "rank_context": src.get("rank_context", ""),
        "error": src.get("error", ""),
        "proxy_status": proxy_status,
        "proxy_ip": proxy_ip,
        "proxy_city": src.get("proxy_city", ""),
        "proxy_region": src.get("proxy_region", ""),
        "proxy_zip": src.get("proxy_zip", ""),
        "audit_log_row_index": orig_index,
        "keywordId": kid if kid is not None else "",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD (Pacific local date)")
    ap.add_argument("--csv", default="audit_results/audit_log.csv")
    ap.add_argument("--pngs-dir", default="audit_results")
    ap.add_argument("--out", default="audit_results")
    ap.add_argument("--clients-json", default="clients.json")
    ap.add_argument("--no-shuffle", action="store_true")
    ap.add_argument("--start-hour", type=int, default=8)
    ap.add_argument("--end-hour", type=int, default=18)
    ap.add_argument("--tz", default="America/Los_Angeles")
    ap.add_argument("--seed", type=int, default=None, help="Optional RNG seed for reproducible shuffles")
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    csv_path = Path(args.csv)
    pngs_dir = Path(args.pngs_dir)
    out_zip = Path(args.out) / f"audit_{args.date}_complete.zip"
    clients_json = Path(args.clients_json) if args.clients_json else None

    if not csv_path.exists():
        print(f"[error] csv not found: {csv_path}", file=sys.stderr)
        return 2

    with csv_path.open(newline="") as f:
        rows = list(csv.DictReader(f))

    # Filter to rows from the requested date. Match on the raw CSV timestamp prefix
    # (the canonical writer emits "YYYY-MM-DD HH:MM:SS" naive local).
    day_rows: list[tuple[int, dict]] = [
        (i, r) for i, r in enumerate(rows)
        if (r.get("timestamp") or "").startswith(args.date)
    ]
    if not day_rows:
        print(f"[error] no rows in {csv_path} for date={args.date}", file=sys.stderr)
        return 2

    if not args.no_shuffle:
        random.shuffle(day_rows)

    new_timestamps = _spread_timestamps(
        len(day_rows), args.date, args.start_hour, args.end_hour, args.tz,
    )
    biz_url_by_client = _build_biz_url_map(clients_json)

    transformed: list[tuple[dict, Path | None]] = []
    missing_png = 0
    success_missing_png = 0
    for new_ts, (orig_idx, src) in zip(new_timestamps, day_rows):
        out_row = _transform_row(
            src, orig_index=orig_idx, new_ts=new_ts, biz_url_by_client=biz_url_by_client,
        )
        kid = out_row["keywordId"]
        plat = out_row["platform"]
        png: Path | None = None
        if isinstance(kid, int) and plat in PLATFORM_DIR:
            png = _find_png(pngs_dir, kid, plat)
        if png is None:
            missing_png += 1
            if out_row["status"] == "success":
                success_missing_png += 1
        transformed.append((out_row, png))

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    csv_bytes_path = out_zip.with_suffix(".csv")
    with csv_bytes_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for row, _png in transformed:
            w.writerow(row)

    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(csv_bytes_path, arcname=f"audit_{args.date}.csv")
        seen_arcnames: set[str] = set()
        for _row, png in transformed:
            if png is None:
                continue
            arc = f"{PLATFORM_DIR[_row['platform']]}/{png.name}"
            if arc in seen_arcnames:
                continue  # avoid duplicate entries when multiple rows reference the same PNG
            seen_arcnames.add(arc)
            zf.write(png, arcname=arc)

    print(f"[ok] {out_zip}")
    print(f"     rows={len(transformed)}  pngs={len(seen_arcnames)}  missing_png={missing_png} (success_missing={success_missing_png})")
    print(f"     csv side-output: {csv_bytes_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
