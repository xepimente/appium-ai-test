Run the AEO audit ranking flow. Follow these steps in order:

1. Run health check (check Runner API, Device Manager, adb devices, Socksdroid, proxy creds). DeepSeek key is NOT needed for audit.
2. Run `cd ~/projects/aeo-appium && python3 setup_devices.py --assign` to assign healthy devices. Show device pool.
3. Show audit plan: fetch clients from `curl -s http://localhost:5001/clients`, show keywords x 3 platforms. The script only uses as many devices as there are keywords — not all devices. Show "Devices available: N | Devices needed: N". Ask user to confirm.
4. Execute: `cd ~/projects/aeo-appium && python3 audit.py --all-devices`. Wait for completion.
5. Show results from audit_results/audit_log.json.

If user specifies options, pass them: --serial {serial}, --clients N, --platform Gemini, --brand Infinix, --reset.
$ARGUMENTS