#!/bin/bash
# launch_scrcpy.sh — Tile healthy assigned devices on screen using scrcpy.
# Uses active_devices.json if present (run: python setup_devices.py --assign).
# Falls back to all connected adb devices if active_devices.json does not exist.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ACTIVE_FILE="$SCRIPT_DIR/active_devices.json"

WINDOW_W=320
WINDOW_H=568
COLS=4

# Resolve serials to full adb transport names using Python
if [[ -f "$ACTIVE_FILE" ]]; then
    echo "Using active_devices.json"
    SERIALS=$(python3 - <<'PYEOF'
import subprocess, json, sys

result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
transport_map = {}
for line in result.stdout.splitlines()[1:]:
    parts = line.strip().split()
    if len(parts) >= 2 and parts[1] == "device":
        transport = parts[0]
        if transport.startswith("adb-") and "._adb-tls-connect" in transport:
            inner = transport[4:]
            inner = inner.split("._adb-tls-connect")[0]
            short = inner.rsplit("-", 1)[0]
            transport_map[short] = transport
        else:
            transport_map[transport] = transport

with open("active_devices.json") as f:
    active = json.load(f)

for label, info in active.items():
    short = info["serial"]
    full = transport_map.get(short)
    if full:
        print(full)
    else:
        print(f"SKIP:{label}:{short}", file=sys.stderr)
PYEOF
)
else
    echo "No active_devices.json found — launching all connected devices"
    SERIALS=$(adb devices | awk 'NR>1 && $2=="device" {print $1}')
fi

if [[ -z "$SERIALS" ]]; then
    echo "No devices to launch."
    exit 1
fi

echo ""
i=0
while IFS= read -r serial; do
    col=$((i % COLS))
    row=$((i / COLS))
    x=$((col * WINDOW_W))
    y=$((row * WINDOW_H))

    scrcpy \
        --serial "$serial" \
        --window-title "Device $((i + 1)) — $serial" \
        --window-x "$x" \
        --window-y "$y" \
        --window-width "$WINDOW_W" \
        --window-height "$WINDOW_H" \
        --no-audio \
        --stay-awake \
        &

    echo "Launched device $((i + 1)): $serial at ($x, $y)"
    sleep 0.3
    i=$((i + 1))
done <<< "$SERIALS"

echo ""
echo "Launched $i scrcpy windows (${COLS} columns x $(( (i + COLS - 1) / COLS )) rows)"
echo "Press Ctrl+C to close all."
wait
