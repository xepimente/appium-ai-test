Run the AEO daily sessions flow. Follow these steps in order:

1. Run health check (check Runner API, Device Manager, adb devices, Socksdroid, proxy creds, DeepSeek key).
2. Run `cd ~/projects/aeo-appium && python3 setup_devices.py --assign` to assign healthy devices. Show device pool. Ask user to confirm.
3. Run `cd ~/projects/aeo-appium && python3 main.py --dry-run` to show session plan. Ask user to confirm.
4. Execute: `cd ~/projects/aeo-appium && python3 main.py`. Wait for completion.
5. Show results: `cd ~/projects/aeo-appium && python3 main.py --status`.
$ARGUMENTS