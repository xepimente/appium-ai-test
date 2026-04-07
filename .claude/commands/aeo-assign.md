Assign healthy devices to the AEO device pool. Run:
```bash
cd ~/projects/aeo-appium && python3 setup_devices.py --assign
```
For devices missing Socksdroid, try ONE install: `adb -s {serial} install ~/app/device-manager/conf/apk/socksdroid/base.apk`. If it fails, skip. Then re-run --assign. Show the final device pool.