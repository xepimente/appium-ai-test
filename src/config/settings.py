"""
src/config/settings.py
Centralized configuration loaded from environment variables.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class AppiumConfig:
    """Appium server and device configuration."""
    host: str = field(default_factory=lambda: os.getenv("APPIUM_HOST", "localhost"))
    port: int = field(default_factory=lambda: int(os.getenv("APPIUM_PORT", "4723")))

    # Android Device capabilities
    device_udid: str = field(default_factory=lambda: os.getenv("DEVICE_UDID", ""))
    device_name: str = field(default_factory=lambda: os.getenv("DEVICE_NAME", "Android Device"))
    platform_version: str = field(default_factory=lambda: os.getenv("PLATFORM_VERSION", "14.0"))
    chrome_driver_version: str = field(default_factory=lambda: os.getenv("CHROME_DRIVER_VERSION", ""))

    @property
    def server_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def get_capabilities(self) -> dict:
        """
        Build Appium desired capabilities for Chrome on Android.
        These tell Appium WHICH device to use and HOW to control it.
        """
        caps = {
            # ── Platform ──────────────────────────────────────────
            "platformName": "Android",
            "appium:platformVersion": self.platform_version,
            "appium:deviceName": self.device_name,
            "appium:udid": self.device_udid,

            # ── Automation Engine ──────────────────────────────────
            # UiAutomator2 is the standard Android automation engine
            "appium:automationName": "UiAutomator2",

            # ── Browser ────────────────────────────────────────────
            # Setting browserName to "Chrome" tells Appium to open
            # Chrome instead of launching an app package.
            "browserName": "Chrome",

            # ── Timeouts & Behavior ────────────────────────────────
            "appium:newCommandTimeout": 120,          # Seconds before session expires
            "appium:noReset": True,                    # Don't reset app state between tests
            "appium:autoGrantPermissions": True,       # Auto-grant Chrome permissions
            "appium:ignoreHiddenApiPolicyError": True, # Ignore WRITE_SECURE_SETTINGS error on physical devices
            "appium:uiautomator2ServerLaunchTimeout": 60000,  # 60s for physical device instrumentation startup (default 30s)
        }

        if self.chrome_driver_version:
            caps["appium:chromedriverVersion"] = self.chrome_driver_version

        return caps


@dataclass
class LLMConfig:
    """Claude LLM configuration."""
    api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"))
    max_tokens: int = 2048

    def validate(self):
        if not self.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. "
                "Add it to your .env file or export it as an environment variable."
            )


@dataclass
class TestConfig:
    """Test behavior configuration."""
    target_url: str = field(default_factory=lambda: os.getenv("TARGET_URL", "https://example.com"))
    search_query: str = field(default_factory=lambda: os.getenv("SEARCH_QUERY", "example website"))
    scroll_pause_seconds: float = field(
        default_factory=lambda: float(os.getenv("SCROLL_PAUSE_SECONDS", "1.5"))
    )
    max_scroll_attempts: int = field(
        default_factory=lambda: int(os.getenv("MAX_SCROLL_ATTEMPTS", "20"))
    )
    screenshot_dir: str = field(
        default_factory=lambda: os.getenv("SCREENSHOT_DIR", "screenshots")
    )
    implicit_wait_seconds: int = field(
        default_factory=lambda: int(os.getenv("IMPLICIT_WAIT_SECONDS", "10"))
    )


# ── Singleton instances ────────────────────────────────────────────────
appium_config = AppiumConfig()
llm_config = LLMConfig()
test_config = TestConfig()
