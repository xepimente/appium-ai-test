"""
src/utils/screenshot.py

Handles capturing, saving, and encoding screenshots from the Android device.

Screenshots serve two purposes:
  1. Evidence/debugging — saved as PNG files for test reports
  2. LLM input — encoded as base64 so Claude can "see" the screen
"""

import os
import base64
import time
from pathlib import Path
from typing import Optional

from PIL import Image
from loguru import logger
from appium.webdriver.webdriver import WebDriver

from src.config.settings import test_config


class ScreenshotManager:
    """Captures and manages screenshots from the Android device."""

    def __init__(self, driver: WebDriver, session_name: str = "test"):
        self.driver = driver
        self.session_name = session_name
        self.screenshot_dir = Path(test_config.screenshot_dir) / session_name
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_count = 0
        logger.info(f"Screenshots will be saved to: {self.screenshot_dir}")

    def capture(self, label: str = "") -> dict:
        """
        Capture a screenshot from the device.

        Args:
            label: Descriptive label (e.g., "after_search", "scroll_3")

        Returns:
            dict with keys:
                - path: Local file path of saved PNG
                - base64: Base64-encoded PNG for sending to Claude
                - label: The label provided
                - timestamp: When it was taken
        """
        self.screenshot_count += 1
        timestamp = int(time.time())
        safe_label = label.replace(" ", "_").lower() if label else f"screenshot_{self.screenshot_count}"
        filename = f"{self.screenshot_count:03d}_{safe_label}_{timestamp}.png"
        filepath = self.screenshot_dir / filename

        try:
            # Appium's get_screenshot_as_base64() returns base64 directly
            # This is more efficient than saving then re-reading
            screenshot_b64 = self.driver.get_screenshot_as_base64()

            # Save to disk for test reports
            self.driver.get_screenshot_as_file(str(filepath))
            logger.debug(f"📸 Screenshot saved: {filepath.name}")

            return {
                "path": str(filepath),
                "base64": screenshot_b64,
                "label": label,
                "timestamp": timestamp,
                "filename": filename,
            }

        except Exception as e:
            logger.error(f"Failed to capture screenshot: {e}")
            raise

    def capture_and_crop(self, label: str, region: tuple) -> dict:
        """
        Capture a screenshot and crop to a specific region.

        Args:
            label: Descriptive label
            region: (x, y, width, height) in pixels

        Returns:
            Same dict as capture() but with cropped image
        """
        result = self.capture(label)
        x, y, w, h = region

        img = Image.open(result["path"])
        cropped = img.crop((x, y, x + w, y + h))
        cropped.save(result["path"])

        # Re-encode cropped image to base64
        with open(result["path"], "rb") as f:
            result["base64"] = base64.b64encode(f.read()).decode("utf-8")

        return result

    def get_screen_dimensions(self) -> tuple[int, int]:
        """Return (width, height) of the device screen."""
        size = self.driver.get_window_size()
        return size["width"], size["height"]
