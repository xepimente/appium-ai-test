#!/bin/bash
# check_devices.sh — Test which devices need "USB debugging (Security Settings)" enabled

check_device() {
    local serial="$1"
    local idx="$2"

    local model brand result

    model=$(timeout 5 adb -s "$serial" shell getprop ro.product.model 2>/dev/null | tr -d '\r')
    brand=$(timeout 5 adb -s "$serial" shell getprop ro.product.brand 2>/dev/null | tr -d '\r')

    # Try injecting a harmless tap — fails with INJECT_EVENTS if security setting is off
    result=$(timeout 5 adb -s "$serial" shell input tap 0 0 2>&1)

    if echo "$result" | grep -q "INJECT_EVENTS"; then
        echo "  [$idx] ❌ NEEDS FIX  | $brand $model | $serial"
    else
        echo "  [$idx] ✅ OK          | $brand $model | $serial"
    fi
}

echo ""
echo "Checking USB debugging (Security Settings) on all connected devices..."
echo "======================================================================="

# Collect all serials first
mapfile -t SERIALS < <(adb devices | awk 'NR>1 && $2=="device" {print $1}')

# Run checks in parallel (one background job per device)
for i in "${!SERIALS[@]}"; do
    check_device "${SERIALS[$i]}" "$((i + 1))" &
done

# Wait for all checks to finish
wait

echo ""
echo "--- How to fix devices marked ❌ ---"
echo ""
echo "General path (all brands):"
echo "  Settings → Developer Options → 'USB debugging (Security Settings)'  ← second toggle"
echo "  Then REBOOT the device."
echo ""
echo "Brand-specific paths:"
echo "  Xiaomi/MIUI   : Settings → Additional Settings → Developer Options"
echo "                  → 'USB debugging (Security Settings)'"
echo "  OPPO/Realme   : Settings → Additional Settings → Developer Options"
echo "                  → 'Disable permission monitoring'"
echo "  Infinix/TECNO : Settings → System → Developer Options"
echo "                  → 'USB debugging (Security Settings)'"
echo "  Samsung       : Settings → Developer Options → toggle USB debugging off/on → Reboot"
echo "                  (Samsung uses a different permission model)"
echo "  Vivo/iQOO     : Settings → Developer Options → 'USB debugging (Security Settings)'"
