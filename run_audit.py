"""Parallel AEO Ranking Audit — 5 concurrent, gost+proxy, paste input."""
import json, os, sys, time, re, random
from concurrent.futures import ThreadPoolExecutor, as_completed

os.environ['AEO_SKIP_PREFLIGHT'] = '1'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from proxy import enrich_proxy_config, setup_device, teardown_device
from gost_manager import GostManager
from audit import audit_gemini_adb, audit_chatgpt_adb, audit_perplexity_adb, build_audit_prompt, extract_ranking

clients_data = json.load(open('clients_for_audit.json'))
devices_all = json.load(open('active_devices.json'))
device_ids = sorted(devices_all.keys())

jobs = []
for c in clients_data:
    addr = str(c.get('biz_address', ''))
    zm = re.search(r'\b\d{5}\b', addr)
    biz_zip = zm.group(0) if zm else '10001'
    for kw in c['keywords']:
        jobs.append({"client": c, "kw": kw['keyword'], "zip": biz_zip})

random.seed(42)
random.shuffle(jobs)

AUDIT_FLOWS = {
    "Gemini": audit_gemini_adb,
    "ChatGPT": audit_chatgpt_adb,
    "Perplexity": audit_perplexity_adb,
}
PLATFORMS = ["Gemini", "ChatGPT", "Perplexity"]

print(f"Audit: {len(jobs)} keywords × 3 platforms = {len(jobs)*3} audits")
print(f"Devices: {len(device_ids)} — 5 concurrent\n")

results_lock = __import__('threading').Lock()
all_results = []
seq = 0

def run_one(job, device_id, gost_key, port):
    dev = devices_all[device_id]
    client = job["client"]
    kw = job["kw"]

    lat, lng = {"NY": (40.71, -74.01), "CA": (34.05, -118.24), "TX": (29.76, -95.37),
                "FL": (30.42, -87.22), "IL": (41.88, -87.63), "PA": (39.95, -75.17),
                "OH": (41.50, -81.69), "GA": (33.75, -84.39), "NC": (35.23, -80.84),
                "AZ": (33.45, -112.07), "MA": (42.36, -71.06), "MI": (42.33, -83.05),
                "VA": (37.54, -77.43), "WA": (47.61, -122.33), "OR": (45.52, -122.68),
                "CO": (39.74, -104.99), "MN": (44.98, -93.27), "WI": (43.04, -87.91),
                "MD": (39.29, -76.61), "MO": (38.63, -90.20), "IN": (39.77, -86.16),
                "KY": (38.25, -85.76), "TN": (36.16, -86.78), "SC": (34.00, -81.03),
                "LA": (29.95, -90.07), "AL": (33.52, -86.80), "OK": (35.47, -97.52),
                "CT": (41.77, -72.67), "UT": (40.76, -111.89), "RI": (41.82, -71.41),
                "NH": (42.99, -71.46), "NJ": (40.73, -74.17)}.get(client.get("state", "NY"), (40.71, -74.01))

    proxy_cfg = {
        "country": "us", "session_duration": 30, "zip": job["zip"],
        "timezone": "America/Chicago",
        "latitude": lat, "longitude": lng,
    }
    proxy_cfg = enrich_proxy_config(proxy_cfg, {"biz_zip": job["zip"]})
    spec = {"device_id": gost_key, "zip": job["zip"], "state": client.get("state", ""),
            "country": "us", "session_duration": 30}
    gost = GostManager([spec], base_port=port)
    gost.start(wait_seconds=2.0)
    proxy_cfg["gost"] = gost.mapping[gost_key]

    try:
        setup = setup_device(dev["serial"], proxy_cfg)
        if setup["proxy"].get("status") == "ERROR":
            return {"success": False, "error": setup["proxy"].get("error")}

        prompt = build_audit_prompt({**client, "keyword": kw})
        plat_results = {}

        for i, plat in enumerate(PLATFORMS):
            t0 = time.time()
            try:
                fn = AUDIT_FLOWS[plat]
                ss_path, text_path, ts = fn(dev["serial"], client, kw, prompt,
                                            cdp_port=9222, is_first=(i == 0))
                with open(text_path) as f:
                    text = f.read()
                ranking = extract_ranking(text, client.get("biz_name", ""),
                                          client.get("biz_url", ""))
                plat_results[plat] = {
                    "success": True, "screenshot": ss_path,
                    "position": ranking.get("position"),
                    "total": ranking.get("total"),
                    "duration": round(time.time() - t0, 1),
                }
            except Exception as e:
                plat_results[plat] = {"success": False, "error": str(e),
                                      "duration": round(time.time() - t0, 1)}

        teardown_device(dev["serial"])
        return {"success": all(p.get("success") for p in plat_results.values()),
                "platforms": plat_results}

    finally:
        gost.stop()

# Rolling dispatch
with ThreadPoolExecutor(max_workers=5) as pool:
    futures = {}
    pending = list(jobs)
    device_cursor = 0
    last_dispatch = 0

    while pending or futures:
        while pending and len(futures) < 5:
            if len(futures) > 0:
                elapsed = time.time() - last_dispatch
                if elapsed < 30:
                    time.sleep(30 - elapsed)
            job = pending.pop(0)
            did = device_ids[device_cursor % len(device_ids)]
            device_cursor += 1
            seq += 1
            gost_key = f"audit-{seq}"
            port = 15001 + (seq % 50) * 10
            f = pool.submit(run_one, job, did, gost_key, port)
            futures[f] = (seq, job, did)
            print(f"[#{seq}] {did} ← {job['kw'][:40]:40} ({job['client']['biz_name'][:25]})")

        for f in as_completed(futures):
            seq_n, job, did = futures.pop(f)
            try:
                r = f.result()
                all_results.append({"keyword": job["kw"], "client": job["client"]["biz_name"],
                                     "device": did, "result": r})
                if r.get("success"):
                    ranks = []
                    for p in PLATFORMS:
                        pr = r["platforms"].get(p, {})
                        pos = pr.get("position", "?")
                        ranks.append(f"{pos}")
                    total_ok = sum(1 for x in all_results if x["result"].get("success"))
                    print(f"[#{seq_n}] PASS {did} ranks={'/'.join(ranks)} | {total_ok}/{len(all_results)}")
                else:
                    print(f"[#{seq_n}] FAIL {did} | {sum(1 for x in all_results if x['result'].get('success'))}/{len(all_results)}")
            except Exception as e:
                print(f"[#{seq_n}] ERROR {did}: {e}")
                all_results.append({"keyword": job["kw"], "client": job["client"]["biz_name"],
                                     "device": did, "result": {"success": False, "error": str(e)}})
            break

# Save
ts = time.strftime('%Y%m%d_%H%M%S')
with open(f"audit_results/audit_run_{ts}.json", 'w') as f:
    json.dump(all_results, f, indent=2, default=str)
ok = sum(1 for r in all_results if r["result"].get("success"))
print(f"\nDone: {ok}/{len(all_results)} passed | audit_results/audit_run_{ts}.json")
