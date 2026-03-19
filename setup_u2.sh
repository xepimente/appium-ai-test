#!/bin/bash
# ── AEO u2 Runner Setup ──────────────────────────────────────────────────
# Run this once on the Mac Mini to set up uiautomator2
#
# Prerequisites:
#   - Python 3.8+
#   - ADB installed and both devices connected
#   - Both devices have USB debugging enabled

echo ""
echo "========================================"
echo "  AEO u2 Runner — Setup"
echo "========================================"
echo ""

# Step 1: Install Python packages
echo "[1/4] Installing Python packages..."
pip3 install uiautomator2 flask --break-system-packages 2>/dev/null || pip3 install uiautomator2 flask

# Step 2: Check ADB devices
echo ""
echo "[2/4] Checking ADB devices..."
adb devices -l
echo ""

# Step 3: Init u2 agent on each device
# This installs the ATX agent app on the device (one-time)
echo "[3/4] Installing u2 agent on devices..."
echo ""

DEVICE_1="324651961440"
DEVICE_2="0B64C27G23101E10"

echo "  Initializing device-001 ($DEVICE_1)..."
python3 -m uiautomator2 init --serial $DEVICE_1

echo ""
echo "  Initializing device-002 ($DEVICE_2)..."
python3 -m uiautomator2 init --serial $DEVICE_2

# Step 4: Verify
echo ""
echo "[4/4] Verifying connections..."
python3 -c "
import uiautomator2 as u2
for serial in ['$DEVICE_1', '$DEVICE_2']:
    try:
        d = u2.connect(serial)
        info = d.info
        print(f'  {serial}: {info.get(\"productName\", \"ok\")} — READY')
    except Exception as e:
        print(f'  {serial}: FAILED — {e}')
"

echo ""
echo "========================================"
echo "  Setup complete!"
echo ""
echo "  Start the runner:"
echo "    python3 u2_runner.py"
echo ""
echo "  Test a single device:"
echo "    python3 u2_flows.py $DEVICE_1 Gemini 'best plumber in San Francisco'"
echo ""
echo "  Test both devices via API:"
echo "    curl -X POST http://localhost:5001/rotation/reset"
echo "    curl -X POST http://localhost:5001/run-all -H 'Content-Type: application/json' -d '{\"sessions\": [...]}'"
echo "========================================"