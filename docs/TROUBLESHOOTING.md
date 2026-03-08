# Troubleshooting & Advanced Guide

## 🔧 Common Issues & Fixes

### Issue 1: `adb devices` shows nothing
```
No devices/emulators found
```
**Fix:**
```bash
# Restart ADB server
adb kill-server
adb start-server
adb devices

# Physically unplug and replug USB cable
# Check USB mode on phone — set to "File Transfer" not "Charging only"
# On phone: Developer Options → Revoke USB debugging authorizations → re-authorize
```

---

### Issue 2: Appium `ChromeDriver` version mismatch
```
ChromeDriver only supports Chrome version XX
```
**Fix:**
```bash
# Check Chrome version on device
adb shell dumpsys package com.android.chrome | grep versionName

# Download matching ChromeDriver from:
# https://chromedriver.chromium.org/downloads
# Place it in your PATH or set chromedriverExecutable in capabilities

# OR let Appium auto-download:
appium driver install uiautomator2 --chromedriver-version 114
```

---

### Issue 3: `SessionNotCreatedException`
```
An unknown server-side error occurred while processing the command
```
**Fix checklist:**
1. Is Appium server running? `appium --port 4723`
2. Is device connected? `adb devices`
3. Is `PLATFORM_VERSION` in `.env` correct? Check: `adb shell getprop ro.build.version.release`
4. Is `DEVICE_UDID` correct? Match exactly from `adb devices` output

---

### Issue 4: Google search box not found
```
NoSuchElementException: no such element
```
**Fix:** Google occasionally updates their UI. Try these selectors manually:
```python
# In Python shell with driver available:
driver.find_elements(By.TAG_NAME, "input")  # List all inputs
driver.find_elements(By.TAG_NAME, "textarea")  # Google now uses textarea
```

---

### Issue 5: Claude API key error
```
AuthenticationError: invalid_api_key
```
**Fix:**
```bash
# Verify key is set
echo $ANTHROPIC_API_KEY

# Or check .env file
cat .env | grep ANTHROPIC

# Get a valid key from: https://console.anthropic.com
```

---

### Issue 6: Page doesn't scroll
**Reason:** Some pages use custom scroll containers instead of `window`.
**Fix:** Try JavaScript scroll on the scroll container:
```python
# In scroll_helper.py, replace execute_script with:
driver.execute_script("document.querySelector('main').scrollBy(0, 500)")
# Find the scroll container using Chrome DevTools on desktop first
```

---

## 🏗️ Advanced: Running on a Real Device vs Emulator

### Real Device (Recommended for accuracy)
```bash
# Connect via USB
adb devices
# Set in .env:
DEVICE_UDID=<your-device-id>  # e.g., R9FW903XXXXX
DEVICE_NAME=Samsung Galaxy S21
PLATFORM_VERSION=13.0
```

### Android Emulator (Easier setup)
```bash
# Create AVD in Android Studio:
# Tools → AVD Manager → Create Virtual Device → Pixel 6 → API 33

# Start emulator
emulator -avd Pixel_6_API_33

# In .env:
DEVICE_UDID=emulator-5554
DEVICE_NAME=Android Emulator
PLATFORM_VERSION=13.0
```

---

## 🔬 Advanced: Extending the Agent

### Adding a custom website-specific extractor
```python
# In src/agents/content_reader_agent.py, add:

def read_product_listings(self) -> list[dict]:
    """Extract product cards from an e-commerce page."""
    products = []
    for scroll_pos in self.scroller.scroll_page_top_to_bottom():
        screenshot = self.screenshots.capture(f"products_{scroll_pos}")
        response = self.claude.analyze_screenshot(
            screenshot["base64"],
            """Extract all product cards visible. For each product return:
            {"name": "...", "price": "...", "rating": "..."}
            Return as JSON array."""
        )
        try:
            items = json.loads(response)
            products.extend(items)
        except json.JSONDecodeError:
            pass
    return products
```

### Adding retry logic
```python
from tenacity import retry, stop_after_attempt, wait_exponential

class BrowserAgent:
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def navigate_to_url(self, url: str) -> bool:
        # ... existing code
```

### Running tests in parallel (multiple devices)
```bash
# Install pytest-xdist (already in requirements.txt)
# Connect multiple devices

# Create separate .env files per device:
# .env.device1, .env.device2

# Run in parallel:
pytest -n 2 --dist=loadfile
```

---

## 📊 Understanding the Test Report

After running tests, find reports in:
```
reports/
├── report.html                  ← pytest HTML report (open in browser)
└── page_content_<timestamp>.txt ← Full extracted page content

screenshots/
├── browser_agent/               ← Screenshots from BrowserAgent
└── content_reader/              ← Screenshots from ContentReaderAgent
```

The HTML report shows:
- Test pass/fail status
- Screenshots attached to each test
- Timing per test
- Console output

---

## 🔄 CI/CD Integration (GitHub Actions)

```yaml
# .github/workflows/mobile-test.yml
name: Mobile Browser Test

on: [push, pull_request]

jobs:
  test:
    runs-on: macos-latest  # macOS required for Android emulator in CI

    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Set up Java
        uses: actions/setup-java@v3
        with:
          distribution: 'temurin'
          java-version: '17'

      - name: Install Android SDK
        uses: android-actions/setup-android@v2

      - name: Create AVD
        run: |
          echo "y" | $ANDROID_HOME/tools/bin/sdkmanager "system-images;android-33;google_apis;x86_64"
          echo "no" | $ANDROID_HOME/tools/bin/avdmanager create avd -n test_avd -k "system-images;android-33;google_apis;x86_64"

      - name: Start Emulator
        run: |
          $ANDROID_HOME/emulator/emulator -avd test_avd -no-audio -no-window &
          adb wait-for-device shell 'while [[ -z $(getprop sys.boot_completed) ]]; do sleep 1; done'

      - name: Install Node.js & Appium
        run: |
          npm install -g appium@2.x
          appium driver install uiautomator2
          appium &

      - name: Install Python deps
        run: pip install -r requirements.txt

      - name: Run Tests
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          DEVICE_UDID: emulator-5554
          TARGET_URL: https://example.com
        run: pytest src/tests/ -v --html=reports/report.html

      - name: Upload report
        uses: actions/upload-artifact@v3
        if: always()
        with:
          name: test-report
          path: reports/
```
