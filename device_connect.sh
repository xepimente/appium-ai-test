#!/bin/bash
# AEO Device Auto-Connect & Keepalive
# Usage: ./device_connect.sh
# - Discovers devices via mDNS
# - Connects all found devices
# - Keeps connections alive with periodic pings
# - Auto-reconnects on disconnect

KNOWN_IPS=("192.168.254.250" "192.168.254.251" "192.168.254.252" "192.168.254.253")
PING_INTERVAL=20
CHECK_INTERVAL=30

connect_devices() {
    echo "=== Scanning for devices ==="

    # Method 1: Try mDNS discovery
    MDNS_DEVICES=$(adb mdns services 2>/dev/null | grep "adb-tls-connect" | awk '{print $3}')
    for dev in $MDNS_DEVICES; do
        echo "  Found via mDNS: $dev"
        adb connect "$dev" 2>/dev/null
    done

    # Method 2: Try known IPs with port scan
    for ip in "${KNOWN_IPS[@]}"; do
        # Check if already connected
        if adb devices 2>/dev/null | grep -q "$ip.*device"; then
            continue
        fi

        # Try common ports
        for port in 5555 36133 37133 38133 39133 40133 41133 42133 43133 44133 45133; do
            if timeout 1 bash -c "echo >/dev/tcp/$ip/$port" 2>/dev/null; then
                echo "  Found port $port on $ip, connecting..."
                result=$(adb connect "$ip:$port" 2>&1)
                if echo "$result" | grep -q "connected"; then
                    echo "  ✓ Connected: $ip:$port"
                    break
                fi
            fi
        done
    done

    echo ""
    echo "=== Connected devices ==="
    adb devices
}

keepalive() {
    while true; do
        DEVICES=$(adb devices 2>/dev/null | grep "device$" | awk '{print $1}')

        if [ -z "$DEVICES" ]; then
            echo "[$(date +%H:%M:%S)] No devices connected, scanning..."
            connect_devices
        else
            for dev in $DEVICES; do
                # Ping each device to keep connection alive
                result=$(adb -s "$dev" shell echo ping 2>&1)
                if echo "$result" | grep -q "ping"; then
                    : # alive
                else
                    echo "[$(date +%H:%M:%S)] Lost $dev, reconnecting..."
                    adb connect "$dev" 2>/dev/null
                fi
            done
        fi

        sleep $PING_INTERVAL
    done
}

echo "AEO Device Manager"
echo "==================="
echo ""

# Kill any stale ADB server and restart clean
ADB_MDNS=0 adb start-server 2>/dev/null

connect_devices

echo ""
echo "Starting keepalive (ping every ${PING_INTERVAL}s)..."
echo "Press Ctrl+C to stop"
echo ""

keepalive
