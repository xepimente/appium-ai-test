#!/usr/bin/env bash
set -e

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║        Appium AI Test — Dev Container Setup              ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── Python dependencies ────────────────────────────────────────────────────────
echo "[1/4] Installing Python dependencies..."
sudo python3 -m pip install --no-cache-dir --upgrade --ignore-installed --break-system-packages -r requirements.txt
echo "      Done."
echo ""

# ── Environment file ───────────────────────────────────────────────────────────
echo "[2/4] Setting up .env..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "      Created .env from .env.example — fill in your values."
else
    echo "      .env already exists, skipping."
fi
echo ""

# ── Wireless ADB ───────────────────────────────────────────────────────────────
echo "[3/4] Wireless ADB setup..."
# Source .env for REMOTE_ADB / ANDROID_DEVICES so they are available
# even if the host didn't export them before opening the devcontainer.
if [ -f ".env" ]; then
    eval "$(grep -v '^#' .env | grep -E '^(REMOTE_ADB|ANDROID_DEVICES|REMOTE_ADB_POLLING_SEC)=' | sed 's/^/export /')"
fi
if [ "${REMOTE_ADB}" = "true" ]; then
    echo "      REMOTE_ADB=true — starting wireless ADB auto-connect..."
    wireless_autoconnect.sh
    wireless_connect.sh
    echo "      Wireless auto-connect daemon started (polling every ${REMOTE_ADB_POLLING_SEC:-5}s)."
else
    echo "      REMOTE_ADB not set — skipping wireless auto-connect."
    echo "      To enable: set REMOTE_ADB=true and ANDROID_DEVICES=<ip>:<port> in .env"
fi
echo ""

# ── Verify tools ───────────────────────────────────────────────────────────────
echo "[4/4] Verifying installed tools..."
echo "      Python:  $(python3 --version)"
echo "      Node:    $(node --version)"
echo "      Java:    $(java -version 2>&1 | head -1)"
echo "      Appium:  $(appium --version)"
echo "      ADB:     $(adb --version | head -1)"
echo ""

# ── Next steps ─────────────────────────────────────────────────────────────────
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║  NEXT STEPS — Wireless Android Device Setup                         ║"
echo "║                                                                      ║"
echo "║  On your Android device (Developer Options):                         ║"
echo "║    Android 11+:  Enable 'Wireless Debugging', note the ip:port       ║"
echo "║    Android ≤10:  Connect USB once, run 'adb tcpip 5555',             ║"
echo "║                  disconnect USB, use ip:5555                         ║"
echo "║                                                                      ║"
echo "║  In your .env file (project root):                                   ║"
echo "║    REMOTE_ADB=true                                                   ║"
echo "║    ANDROID_DEVICES=<device-ip>:<port>                                ║"
echo "║                                                                      ║"
echo "║  Inside this container ADB connects directly to the device over      ║"
echo "║  WiFi — no USB or host ADB daemon needed.                            ║"
echo "║                                                                      ║"
echo "║  Verify the device is visible:                                       ║"
echo "║    adb devices                                                       ║"
echo "║                                                                      ║"
echo "║  Start Appium server (separate terminal):                            ║"
echo "║    appium --port 4723 --log-level info \\                            ║"
echo "║      --allow-insecure uiautomator2:chromedriver_autodownload         ║"
echo "║                                                                      ║"
echo "║  Verify Android connection (run this first):                         ║"
echo "║    pytest src/tests/test_browser_search.py::TestBrowserLaunch -v    ║"
echo "║                                                                      ║"
echo "║  Run tests:                                                          ║"
echo "║    pytest src/tests/ -v --html=reports/report.html                   ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
