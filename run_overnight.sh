#!/bin/bash
# Overnight chain: waits for current Mark run to finish, then runs clients 5, 2, 3 with auto-push.
# Writes per-client logs + a final run_overnight_done.log.

set -u
cd /Users/seolocalph/projects/aeo-appium

echo "[$(date)] Waiting for current Mark run (clientId=4) to finish..."
while pgrep -f "run_client_daily.py 4" > /dev/null; do
    sleep 30
done
echo "[$(date)] Mark finished."

for cid in 5 2 3; do
    case $cid in
        5) name="leo_lapuerta" ;;
        2) name="mary_thornton" ;;
        3) name="russ_thornton" ;;
    esac
    LOG="run_${name}.log"
    echo "[$(date)] ── Starting clientId=$cid ($name) → $LOG ──"
    python3 -u run_client_daily.py $cid --push > "$LOG" 2>&1
    echo "[$(date)] ── clientId=$cid exit=$? ──"
    sleep 10
done

echo "[$(date)] All clients finished. Disconnecting Decodo proxy on every device..."
python3 - <<'PY'
import json, sys
from proxy import teardown_device
with open("active_devices.json") as f:
    pool = json.load(f)
for did, info in pool.items():
    serial = info["serial"]
    try:
        r = teardown_device(serial)
        print(f"  [{did}] teardown: {r.get('status','?')}")
    except Exception as e:
        print(f"  [{did}] teardown error: {e}")
PY

echo "[$(date)] Killing any lingering run_client_daily / python session runner processes..."
pkill -f "run_client_daily.py" 2>/dev/null || true
pkill -f "session_runner"       2>/dev/null || true

echo "[$(date)] ALL CLIENTS DONE (4, 5, 2, 3) — Decodo disconnected on all devices" > run_overnight_done.log
echo "[$(date)] Chain complete. See run_overnight_done.log"
