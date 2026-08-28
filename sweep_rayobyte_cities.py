#!/usr/bin/env python3
"""Refresh rayobyte_no_pool_cities.json — RUN ONLY WHILE THE FLEET IS IDLE.

Rayobyte's account is concurrency-capped: while a ranking run holds sessions, a probe
for a city with a perfectly good pool hangs ~40s and returns nothing, so a sweep taken
during a run would mark the whole catalog dead. Check that no run is live first:

    pgrep -f run_ranking.py     # must print nothing

Usage:
    set -a; source ~/projects/device-agent/.env.dev; set +a
    PROXY_PROVIDER=rayobyte PROXY_HOST=us-east.gw.rayobyte.com PROXY_PORT=8000 \\
      PROXY_BASE_USER=<user> PROXY_PASSWORD=<pass> python3 sweep_rayobyte_cities.py

Reads every city in the audit catalog plus the state metros, probes each once, and
writes the slugs that fast-refuse. Cities whose verdict is "unknown" are left OUT of the
no-pool list — an unproven city still gets city-level geo, which beats a country exit.
"""
import json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gost_manager as g

CATALOG = "/Users/seolocalph/projects/aeo-appium/clients_audit_targets.json"

if subprocess.run(["pgrep", "-f", "run_ranking.py"], capture_output=True).returncode == 0:
    sys.exit("a ranking run is live — its sessions will make every probe read as dead. Aborting.")

cities = set()
try:
    with open(CATALOG) as fh:
        for entry in json.load(fh).values():
            city = g._rayobyte_city_slug(entry.get("city", ""))
            if city:
                cities.add(city)
except Exception as exc:
    print(f"catalog unreadable ({exc}) — sweeping metros only")
cities |= set(g._REGION_METRO_CITY.values())

dead, unknown = [], []
for i, city in enumerate(sorted(cities), 1):
    verdict = g._probe_rayobyte_geo(g.build_rayobyte_geo("us", city))
    if verdict == "no_pool":
        dead.append(city)
    elif verdict == "unknown":
        unknown.append(city)
    print(f"[{i}/{len(cities)}] {city:28s} {verdict}", flush=True)
    time.sleep(0.5)

with open(g.RAYOBYTE_NO_POOL_FILE, "w") as fh:
    json.dump(sorted(dead), fh, indent=1)
print(f"\nwrote {len(dead)} no-pool cities to {g.RAYOBYTE_NO_POOL_FILE}")
if unknown:
    print(f"{len(unknown)} inconclusive (left usable): {', '.join(unknown[:12])}"
          f"{' …' if len(unknown) > 12 else ''}")
