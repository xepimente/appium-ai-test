#!/usr/bin/env bash
set -e

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║        Appium AI Test — Dev Container Setup              ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── Python dependencies ────────────────────────────────────────────────────────
echo "[1/3] Installing Python dependencies..."
sudo python3 -m pip install --no-cache-dir --upgrade --ignore-installed -r requirements.txt
echo "      Done."
echo ""

# ── Environment file ───────────────────────────────────────────────────────────
echo "[2/3] Setting up .env..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "      Created .env from .env.example — fill in your values."
else
    echo "      .env already exists, skipping."
fi
echo ""

# ── Verify tools ───────────────────────────────────────────────────────────────
echo "[3/3] Verifying installed tools..."
echo "      Python:  $(python3 --version)"
echo "      Node:    $(node --version)"
echo "      Java:    $(java -version 2>&1 | head -1)"
echo "      Appium:  $(appium --version)"
echo "      ADB:     $(adb --version | head -1)"
echo ""

# ── Next steps ─────────────────────────────────────────────────────────────────
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║  NEXT STEPS — Physical Android Device Setup                         ║"
echo "║                                                                      ║"
echo "║  On your HOST machine (outside this container):                      ║"
echo "║                                                                      ║"
echo "║  1. Connect Android phone via USB with USB Debugging enabled         ║"
echo "║  2. Accept the RSA key prompt on the phone                           ║"
echo "║  3. Verify connection:  adb devices                                  ║"
echo "║     (run this on your HOST, not inside the container)                ║"
echo "║                                                                      ║"
echo "║  Inside this container, ADB connects to your host's ADB daemon       ║"
echo "║  automatically via host.docker.internal — no extra steps needed.     ║"
echo "║                                                                      ║"
echo "║  To verify the device is visible inside the container:               ║"
echo "║    adb devices                                                       ║"
echo "║                                                                      ║"
echo "║  To start Appium server (in a separate terminal):                    ║"
echo "║    appium --port 4723 --log-level info                               ║"
echo "║                                                                      ║"
echo "║  To run tests:                                                       ║"
echo "║    pytest src/tests/ -v --html=reports/report.html                   ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
