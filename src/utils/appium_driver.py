"""
src/utils/appium_driver.py

Manages creation and teardown of the Appium WebDriver session.

HOW APPIUM WORKS:
─────────────────
1. You start an Appium Server (background process on port 4723)
2. Your Python test creates a "session" by sending capabilities to the server
3. Appium server talks to the Android device via ADB (Android Debug Bridge)
4. Appium tells the UiAutomator2 framework on the device what to do
5. For browser testing, Appium also controls ChromeDriver on the device

Connection chain:
  Python Test → Appium Server → ADB → UiAutomator2 → Android Chrome
"""

import time
from typing import Optional
from loguru import logger

from appium import webdriver
from appium.options.android.uiautomator2.base import UiAutomator2Options
from appium.webdriver.appium_service import AppiumService

from src.config.settings import appium_config, test_config


def create_driver() -> webdriver.Remote:
    """
    Create and return an Appium WebDriver session targeting Chrome on Android.

    Returns:
        webdriver.Remote: Active Appium driver session

    Raises:
        Exception: If connection to Appium server or device fails
    """
    logger.info(f"Creating Appium driver session...")
    logger.info(f"  Server:  {appium_config.server_url}")
    logger.info(f"  Device:  {appium_config.device_name} ({appium_config.device_udid})")
    logger.info(f"  Android: {appium_config.platform_version}")

    # Build capabilities using the typed options class
    # UiAutomator2Options is the recommended way in Appium 2.x
    options = UiAutomator2Options()

    raw_caps = appium_config.get_capabilities()
    for key, value in raw_caps.items():
        # Strip "appium:" prefix for the options object
        clean_key = key.replace("appium:", "")
        try:
            setattr(options, clean_key, value)
        except AttributeError:
            # Some caps need to be set via load_capabilities
            pass

    # Use load_capabilities for any remaining/custom caps
    options.load_capabilities(raw_caps)

    try:
        driver = webdriver.Remote(
            command_executor=appium_config.server_url,
            options=options,
        )

        # Set implicit wait — Appium waits up to N seconds when finding elements
        driver.implicitly_wait(test_config.implicit_wait_seconds)

        logger.success(f"✅ Appium session created: {driver.session_id}")
        return driver

    except Exception as e:
        logger.error(f"❌ Failed to create Appium session: {e}")
        logger.error("Checklist:")
        logger.error("  1. Is Appium server running? Run: appium --port 4723")
        logger.error("  2. Is the device connected? Run: adb devices")
        logger.error(f"  3. Does DEVICE_UDID match? Got: {appium_config.device_udid}")
        logger.error("  4. Is Chrome installed on the device?")
        raise


def quit_driver(driver: Optional[webdriver.Remote]) -> None:
    """Safely quit the Appium driver session."""
    if driver:
        try:
            driver.quit()
            logger.info("Appium driver session closed.")
        except Exception as e:
            logger.warning(f"Error closing driver: {e}")
