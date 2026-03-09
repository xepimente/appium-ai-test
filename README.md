# 🤖 Appium + AI Agent + LLM Mobile Browser Test Framework

A fully automated mobile browser testing framework that uses **Appium** for device control, **AI Agents** for decision-making, and **Claude (LLM)** for intelligent content reading and validation.

---

## 🗺️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        TEST ORCHESTRATOR                        │
│                     (pytest + test runner)                      │
└──────────────────────────┬──────────────────────────────────────┘
                           │
           ┌───────────────▼───────────────┐
           │         AI TEST AGENT          │
           │  (Decision maker & controller) │
           └───┬───────────────────┬───────┘
               │                   │
    ┌──────────▼──────┐   ┌────────▼──────────┐
    │  APPIUM DRIVER  │   │   LLM INTEGRATION  │
    │ (Browser/Device │   │  (Claude API for   │
    │   Automation)   │   │ content analysis)  │
    └──────────┬──────┘   └────────────────────┘
               │
    ┌──────────▼──────────┐
    │   ANDROID DEVICE    │
    │  Chrome/Browser     │
    │  (Real or Emulator) │
    └─────────────────────┘
```

## 📂 Project Structure

```
appium-ai-test/
├── src/
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── browser_agent.py        # AI agent that drives the browser
│   │   └── content_reader_agent.py # AI agent that reads & validates content
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── claude_client.py        # Claude API integration
│   │   └── prompts.py              # LLM prompt templates
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── test_browser_search.py  # Main test file
│   │   └── conftest.py             # pytest fixtures
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── appium_driver.py        # Appium driver setup
│   │   ├── screenshot.py           # Screenshot utilities
│   │   └── scroll_helper.py        # Scroll & content extraction
│   └── config/
│       ├── __init__.py
│       └── settings.py             # Configuration settings
├── reports/                        # HTML test reports
├── screenshots/                    # Captured screenshots
├── requirements.txt
├── pytest.ini
├── .env.example
└── README.md
```

---

## 🧰 Prerequisites

### 1. System Requirements
- **OS**: macOS, Windows, or Linux (macOS recommended for Android testing)
- **Java JDK**: 11 or higher
- **Node.js**: 18+
- **Python**: 3.10+
- **Android SDK**: API level 28+ (Android 9+)

### 2. Android Device/Emulator
- A physical Android phone with **USB Debugging enabled**, OR
- An Android Virtual Device (AVD) via Android Studio

---

## 🚀 Step-by-Step Setup

### Step 1 — Install Java JDK

**macOS:**
```bash
brew install openjdk@17
echo 'export JAVA_HOME=/opt/homebrew/opt/openjdk@17' >> ~/.zshrc
source ~/.zshrc
java -version
```

**Ubuntu/Debian:**
```bash
sudo apt-get install openjdk-17-jdk
echo 'export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64' >> ~/.bashrc
source ~/.bashrc
```

**Windows:**
Download from https://adoptium.net and set `JAVA_HOME` in System Environment Variables.

---

### Step 2 — Install Android SDK & Set Up ADB

**macOS (via Android Studio):**
1. Download [Android Studio](https://developer.android.com/studio)
2. Open Android Studio → SDK Manager → install **Android SDK Platform-Tools**
3. Set environment variables:
```bash
echo 'export ANDROID_HOME=$HOME/Library/Android/sdk' >> ~/.zshrc
echo 'export PATH=$ANDROID_HOME/platform-tools:$PATH' >> ~/.zshrc
echo 'export PATH=$ANDROID_HOME/tools:$PATH' >> ~/.zshrc
source ~/.zshrc
adb version   # Should show adb version
```

**Ubuntu:**
```bash
sudo apt-get install android-tools-adb
# Or via Android Studio SDK path:
echo 'export ANDROID_HOME=$HOME/Android/Sdk' >> ~/.bashrc
echo 'export PATH=$ANDROID_HOME/platform-tools:$PATH' >> ~/.bashrc
source ~/.bashrc
```

---

### Step 3 — Install Appium

```bash
# Install Appium globally via npm
npm install -g appium@2.x

# Install the UiAutomator2 driver (for Android)
appium driver install uiautomator2

# Verify installation
appium driver list --installed

# Optional: Install Appium Doctor to diagnose setup issues
npm install -g @appium/doctor
appium-doctor --android
```

---

### Step 4 — Enable USB Debugging on Android Phone

1. Go to **Settings → About Phone**
2. Tap **Build Number** 7 times to unlock Developer Options
3. Go to **Settings → Developer Options**
4. Enable **USB Debugging**
5. Connect phone to computer via USB
6. Accept the RSA key prompt on your phone

Verify connection:
```bash
adb devices
# Should show:  <device-id>   device
```

---

### Step 5 — Set Up Python Environment

```bash
cd appium-ai-test

# Create virtual environment
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

---

### Step 6 — Configure Environment Variables

```bash
cp .env.example .env
# Edit .env with your values
```

---

### Step 7 — Start Appium Server

```bash
# In a separate terminal window:
# --allow-insecure chromedriver_autodownload ensures the correct ChromeDriver
# version is downloaded or updated automatically to match the Chrome version on
# the connected device.
appium --port 4723 --log-level info --allow-insecure uiautomator2:chromedriver_autodownload

# Appium server should say:
# Appium REST http interface listener started on 0.0.0.0:4723
```

---

### Step 8 — Verify Android Connection

Before running the full test suite, confirm that Appium can connect to your Android device:

```bash
pytest src/tests/test_browser_search.py::TestBrowserLaunch -v
```

A passing result means the device is reachable, ADB is working, and Appium can launch Chrome successfully.

---

### Step 9 — Run the Tests

```bash
# Activate venv first
source venv/bin/activate

# Run all tests
pytest src/tests/ -v --html=reports/report.html

# Run specific test
pytest src/tests/test_browser_search.py::test_search_and_read -v

# Run with live output
pytest src/tests/ -v -s
```

---

## 🧪 How It Works (Test Flow)

```
1. pytest starts → loads fixtures → initializes Appium driver
2. BrowserAgent opens Chrome on Android
3. Agent types search query into URL/search bar
4. LLM analyzes screenshot to verify correct page loaded
5. ContentReaderAgent scrolls page top → bottom
6. At each scroll position, screenshot is captured
7. LLM reads and extracts text content from each screenshot
8. All content is aggregated and validated
9. Test reports are generated
```
