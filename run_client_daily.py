"""Run daily session for any admin client.

Usage:  python3 run_client_daily.py <clientId> [--push]

Fetches the client from admin, runs 5 sessions (random kw + random platform),
connects real Decodo proxy per session (targeted to client's business zip),
writes sessions_log.<slug>.csv with backlinks_found column.

Does NOT push to admin by default (use --push to also POST sessions + bump counts).
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

from session_runner import run_parallel, run_sequential


ADMIN = "https://jjm59vpn3y.us-east-1.awsapprunner.com"
TOKEN = "89d0385d06ab80d79a034745c978a298f4728c85066616f359cb8b9bb87e5644"
H = {"X-Executor-Token": TOKEN, "Content-Type": "application/json"}

PLATFORMS = ["ChatGPT", "Gemini", "Perplexity"]
SESSIONS_PER_CLIENT = 5

SESSION_CSV_COLUMNS = [
    "timestamp", "date", "client_id", "client_name", "biz_name",
    "city", "state", "campaign_id", "campaign_name", "keyword",
    "prompt", "follow_up", "has_follow_up",
    "device_id", "platform", "status", "duration_s",
    "proxy_status", "proxy_username", "proxy_host", "proxy_port",
    "proxy_ip", "proxy_city", "proxy_region", "proxy_country", "proxy_zip",
    "base_latitude", "base_longitude",
    "mocked_latitude", "mocked_longitude", "mocked_timezone",
    "backlinks_expected", "backlink_found", "backlink_url",
    "error",
]


def build_prompt(biz_name: str, biz_city: str, biz_state: str, keyword: str) -> tuple[str, str]:
    """Generate a natural conversational prompt that cites the business +
    asks about the keyword. Encourages AI to mention sources."""
    base = (
        f"I'm looking for recommendations on {keyword} in the {biz_city}, {biz_state} area. "
        f"A friend mentioned {biz_name}. Are they a solid choice for {keyword}, "
        f"or are there stronger options locally? If you can, cite the sources or links you're using."
    )
    follow_up = f"Got any specific examples of their recent work or reviews I can check?"
    return base, follow_up


def extract_zip(addr: str | None) -> str | None:
    if not addr:
        return None
    import re
    m = re.search(r"\b(\d{5})(?:-\d{4})?\b", addr)
    return m.group(1) if m else None


def slugify(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in (s or "").lower()).strip("_")


def extract_session_id(username: str) -> str | None:
    try:
        return username.split("session-", 1)[1].split("-", 1)[0]
    except Exception:
        return None


def main(client_id: int, do_push: bool, parallel: bool) -> int:
    random.seed()

    clients = {c["id"]: c for c in requests.get(f"{ADMIN}/api/clients", headers=H, timeout=30).json()}
    if client_id not in clients:
        print(f"Client {client_id} not found")
        return 1
    client = clients[client_id]
    client_name = client.get("businessName") or f"client-{client_id}"
    print(f"\nClient: {client_name} (id={client_id})")

    businesses = [b for b in requests.get(f"{ADMIN}/api/businesses", headers=H, timeout=30).json()
                  if b.get("clientId") == client_id]
    plans     = [p for p in requests.get(f"{ADMIN}/api/aeo-plans", headers=H, timeout=30).json()
                 if p.get("clientId") == client_id]
    kws       = [k for k in requests.get(f"{ADMIN}/api/keywords?clientId={client_id}",
                                         headers=H, timeout=30).json() if k.get("isActive")]
    print(f"  Businesses: {len(businesses)}  AEO plans: {len(plans)}  Active keywords: {len(kws)}")

    biz_by_id  = {b["id"]: b for b in businesses}
    plan_by_bid = {p["businessId"]: p for p in plans}

    if len(kws) == 0:
        print("No active keywords — nothing to run")
        return 1

    # Pick 5 random keywords (or all if <5)
    pick = random.sample(kws, min(SESSIONS_PER_CLIENT, len(kws)))

    with open("active_devices.json") as f:
        device_pool = json.load(f)
    device_ids = list(device_pool.keys())
    random.shuffle(device_ids)

    # Build sessions
    sessions = []
    meta_rows = []
    for i, kw in enumerate(pick):
        did = device_ids[i % len(device_ids)]
        dev = device_pool[did]
        biz = biz_by_id.get(kw.get("businessId")) or businesses[0]
        plan = plan_by_bid.get(biz["id"])
        biz_zip = extract_zip(plan.get("searchAddress") if plan else biz.get("publishedAddress"))
        biz_lat = biz.get("latitude") or 0.0
        biz_lng = biz.get("longitude") or 0.0

        platform = random.choice(PLATFORMS)
        prompt, follow_up = build_prompt(biz.get("name") or client_name,
                                         biz.get("city") or "", biz.get("state") or "",
                                         kw["keywordText"])

        proxy_cfg = {
            "country":          "us",
            "session_duration": 30,
            "zip":              biz_zip or "10001",
            "timezone":         biz.get("timezone") or "America/New_York",
            "latitude":         biz_lat,
            "longitude":        biz_lng,
        }
        backlink_urls = [l["linkUrl"] for l in (kw.get("links") or [])
                         if l.get("linkActive") and l.get("linkUrl")]
        sessions.append({
            "device_id":   did,
            "serial":      dev["serial"],
            "full_serial": dev["serial"],
            "port":        dev["port"],
            "platform":    platform,
            "platforms":   [platform],
            "prompt":      prompt,
            "follow_up":   follow_up,
            "use_adb":     dev.get("use_adb", True),
            "backlinks":   backlink_urls,
            "proxy":       proxy_cfg,
        })
        meta_rows.append({
            "kw": kw, "biz": biz, "plan": plan, "platform": platform,
            "device_id": did, "prompt": prompt, "follow_up": follow_up,
            "backlinks_expected": len(backlink_urls),
        })
        print(f"  [{i+1}/{len(pick)}] {did}: kid={kw['id']} kw={kw['keywordText']!r} → {platform}  biz={biz['name'][:40]}  zip={biz_zip}  backlinks={len(backlink_urls)}")

    mode = "parallel" if parallel else "sequential"
    eta = "~3-5 min" if parallel else f"~{len(sessions) * 4}-{len(sessions) * 6} min"
    print(f"\nLaunching {len(sessions)} {mode} sessions (ETA {eta})...\n")
    t0 = time.time()
    results = run_parallel(sessions) if parallel else run_sequential(sessions)
    elapsed = time.time() - t0

    # Build CSV rows
    csv_rows = []
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    today = datetime.now(timezone.utc).date().isoformat()
    for sess_meta, res in zip(meta_rows, results):
        kw = sess_meta["kw"]
        biz = sess_meta["biz"]
        plan = sess_meta["plan"]
        pr = (res.get("platform_results") or [{}])[0]
        steps = pr.get("steps") or []
        clicked = next((s for s in steps if isinstance(s, str) and s.startswith("backlink_clicked:")), None)
        proxy_info = res.get("proxy") or {}
        csv_rows.append({
            "timestamp":        now,
            "date":             today,
            "client_id":        client_id,
            "client_name":      client_name,
            "biz_name":         biz.get("name"),
            "city":             biz.get("city"),
            "state":            biz.get("state"),
            "campaign_id":      plan["id"] if plan else "",
            "campaign_name":    plan["name"] if plan else "",
            "keyword":          kw["keywordText"],
            "prompt":           sess_meta["prompt"],
            "follow_up":        sess_meta["follow_up"],
            "has_follow_up":    bool(sess_meta["follow_up"]),
            "device_id":        sess_meta["device_id"],
            "platform":         sess_meta["platform"],
            "status":           "success" if res.get("success") else "error",
            "duration_s":       pr.get("duration_s", ""),
            "proxy_status":     proxy_info.get("status"),
            "proxy_username":   proxy_info.get("username"),
            "proxy_host":       proxy_info.get("proxy_host"),
            "proxy_port":       proxy_info.get("proxy_port"),
            "proxy_ip":         proxy_info.get("ip"),
            "proxy_city":       proxy_info.get("ip_city"),
            "proxy_region":     proxy_info.get("ip_region"),
            "proxy_country":    proxy_info.get("ip_country"),
            "proxy_zip":        proxy_info.get("ip_zip"),
            "base_latitude":    proxy_info.get("base_latitude"),
            "base_longitude":   proxy_info.get("base_longitude"),
            "mocked_latitude":  proxy_info.get("mocked_latitude"),
            "mocked_longitude": proxy_info.get("mocked_longitude"),
            "mocked_timezone":  proxy_info.get("mocked_timezone"),
            "backlinks_expected": sess_meta["backlinks_expected"],
            "backlink_found":   bool(clicked),
            "backlink_url":     clicked.split(":", 1)[1] if clicked else "",
            "error":            pr.get("error", ""),
        })

    slug = slugify(client_name)
    csv_out = f"sessions_log.{slug}.csv"
    with open(csv_out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SESSION_CSV_COLUMNS)
        w.writeheader()
        w.writerows(csv_rows)
    print(f"\n{'='*60}")
    print(f"DONE — elapsed={elapsed:.1f}s")
    print(f"Results:")
    for r, meta in zip(results, meta_rows):
        did = r.get("device_id")
        pr = (r.get("platform_results") or [{}])[0]
        mark = "PASS" if r.get("success") else "FAIL"
        steps = pr.get("steps") or []
        clicked = next((s for s in steps if isinstance(s, str) and s.startswith("backlink_clicked:")), None)
        back = clicked.split(":", 1)[1][:50] if clicked else "-"
        print(f"  [{mark}] {did}  {pr.get('platform'):<12}  {pr.get('duration_s',0)}s  backlink={back}  err={pr.get('error','')}")
    print(f"\nCSV saved: {csv_out}")

    if do_push:
        print(f"\n— Pushing {len(csv_rows)} sessions to admin —")
        push_ok = push_fail = 0
        per_kw_bumps: dict[int, dict] = {}
        for row, meta in zip(csv_rows, meta_rows):
            kid = meta["kw"]["id"]
            bid = meta["biz"]["id"]
            payload = {
                "clientId":       client_id,
                "businessId":     bid,
                "keywordId":      kid,
                "deviceId":       None,
                "proxyId":        None,
                "promptText":     row["prompt"],
                "followupText":   row["follow_up"] if row["has_follow_up"] else None,
                "aiPlatform":     row["platform"].lower(),
                "status":         row["status"],
                "type":           "aeo",
                "proxyUsername":  row["proxy_username"],
                "proxySessionId": extract_session_id(row["proxy_username"] or "") if row["proxy_username"] else None,
            }
            resp = requests.post(f"{ADMIN}/api/sessions", headers=H, json=payload, timeout=30)
            if resp.status_code in (200, 201):
                push_ok += 1
                per_kw_bumps.setdefault(kid, {"initial": 0, "followup": 0})
                per_kw_bumps[kid]["initial"] += 1
                if payload["followupText"]:
                    per_kw_bumps[kid]["followup"] += 1
            else:
                push_fail += 1
                print(f"  POST fail: kid={kid} → HTTP {resp.status_code}: {resp.text[:120]}")

        # Re-fetch current counts for PATCH
        kw_by_id = {k["id"]: k for k in kws}
        patch_ok = 0
        for kid, bumps in per_kw_bumps.items():
            cur = kw_by_id[kid]
            body = {
                "initialSearchCount30Days": (cur.get("initialSearchCount30Days") or 0) + bumps["initial"],
                "initialSearchCountLife":   (cur.get("initialSearchCountLife") or 0) + bumps["initial"],
            }
            if bumps["followup"]:
                body["followupSearchCount30Days"] = (cur.get("followupSearchCount30Days") or 0) + bumps["followup"]
                body["followupSearchCountLife"]   = (cur.get("followupSearchCountLife") or 0) + bumps["followup"]
            r = requests.patch(f"{ADMIN}/api/keywords/{kid}", headers=H, json=body, timeout=30)
            if r.status_code in (200, 201):
                patch_ok += 1
            else:
                print(f"  PATCH fail kid={kid} → {r.status_code}: {r.text[:120]}")
        print(f"\nPush: sessions ok={push_ok}/{len(csv_rows)}  fail={push_fail}")
        print(f"Keyword bumps: ok={patch_ok}/{len(per_kw_bumps)}")

    return 0 if all(r.get("success") for r in results) else 3


if __name__ == "__main__":
    args = sys.argv[1:]
    push = "--push" in args
    parallel = "--parallel" in args  # default: sequential (1 device at a time)
    args = [a for a in args if not a.startswith("--")]
    if len(args) < 1:
        print("usage: run_client_daily.py <clientId> [--push] [--parallel]")
        sys.exit(2)
    sys.exit(main(int(args[0]), do_push=push, parallel=parallel))
