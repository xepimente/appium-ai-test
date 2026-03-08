"""
src/utils/scroll_helper.py

Handles scrolling through a webpage on Android Chrome using Appium.

WHY WE NEED CUSTOM SCROLLING:
──────────────────────────────
On mobile, pages are taller than the screen. To read content top-to-bottom,
we must scroll down incrementally and capture screenshots at each position.

Appium provides several scrolling methods:
  1. W3C Actions API (recommended) — simulates finger swipe gestures
  2. execute_script("mobile: scroll") — native mobile scroll command
  3. TouchAction (deprecated in Appium 2.x)

We use W3C Actions API as it's the most reliable and cross-platform.
"""

import time
from typing import Generator
from loguru import logger

from appium.webdriver.webdriver import WebDriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
from selenium.webdriver.common.actions import interaction

from src.config.settings import test_config


class ScrollHelper:
    """
    Manages page scrolling on Android using Appium W3C touch actions.

    Usage:
        scroller = ScrollHelper(driver)
        for position in scroller.scroll_page_top_to_bottom():
            screenshot = screenshot_manager.capture(f"scroll_{position}")
            # ... process screenshot with LLM
    """

    def __init__(self, driver: WebDriver):
        self.driver = driver
        self.width, self.height = self._get_screen_size()
        self.scroll_amount = int(self.height * 0.6)  # Scroll 60% of screen height per swipe
        self.pause = test_config.scroll_pause_seconds

    def _get_screen_size(self) -> tuple[int, int]:
        size = self.driver.get_window_size()
        return size["width"], size["height"]

    def _perform_swipe_up(self) -> None:
        """
        Simulate a finger swipe UP gesture to scroll the page DOWN.

        On a touchscreen:
          - Swipe UP = content moves UP = user sees content further DOWN the page
          - Start point: lower area of screen (e.g., 70% from top)
          - End point:   upper area of screen (e.g., 20% from top)
        """
        start_x = self.width // 2
        start_y = int(self.height * 0.70)
        end_y = int(self.height * 0.20)

        # W3C Actions: Create a pointer (finger) input
        finger = PointerInput(interaction.POINTER_TOUCH, "finger")
        actions = ActionBuilder(self.driver, mouse=finger)

        # Press finger at start position
        actions.pointer_action.move_to_location(start_x, start_y)
        actions.pointer_action.pointer_down()
        # Pause briefly to register the touch
        actions.pointer_action.pause(0.1)
        # Move finger to end position (scroll up gesture)
        actions.pointer_action.move_to_location(start_x, end_y)
        # Pause to let scroll register
        actions.pointer_action.pause(0.1)
        # Lift finger
        actions.pointer_action.pointer_up()

        actions.perform()
        time.sleep(self.pause)  # Wait for scroll animation to complete

    def scroll_to_top(self) -> None:
        """Navigate to the very top of the page."""
        logger.debug("Scrolling to top of page...")
        # Execute JavaScript to scroll to top (works in Chrome WebView)
        self.driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.5)

    def get_scroll_position(self) -> int:
        """Return current vertical scroll position in pixels."""
        return self.driver.execute_script("return window.pageYOffset;")

    def get_page_height(self) -> int:
        """Return total height of the page in pixels."""
        return self.driver.execute_script("return document.body.scrollHeight;")

    def is_at_bottom(self) -> bool:
        """Check if we've reached the bottom of the page."""
        scroll_pos = self.get_scroll_position()
        viewport_height = self.driver.execute_script("return window.innerHeight;")
        page_height = self.get_page_height()
        return (scroll_pos + viewport_height) >= (page_height - 50)  # 50px tolerance

    def scroll_page_top_to_bottom(self) -> Generator[int, None, None]:
        """
        Generator that scrolls from top to bottom of page.

        Yields the current scroll position (int) at each scroll step.
        Caller can capture screenshots at each yielded position.

        Usage:
            for scroll_pos in scroller.scroll_page_top_to_bottom():
                screenshot = capture_screenshot(f"pos_{scroll_pos}")
        """
        logger.info("Starting top-to-bottom page scroll...")
        self.scroll_to_top()
        time.sleep(0.5)

        scroll_count = 0
        last_position = -1

        # Always capture the initial view (top of page)
        current_pos = self.get_scroll_position()
        yield current_pos

        while scroll_count < test_config.max_scroll_attempts:
            if self.is_at_bottom():
                logger.info(f"✅ Reached bottom of page after {scroll_count} scrolls.")
                break

            self._perform_swipe_up()
            scroll_count += 1
            current_pos = self.get_scroll_position()

            # Detect if page didn't scroll (stuck or bottom reached)
            if current_pos == last_position:
                logger.info("Page position unchanged — reached end.")
                break

            last_position = current_pos
            logger.debug(f"  Scroll {scroll_count}: position={current_pos}px")
            yield current_pos

        else:
            logger.warning(f"Hit max scroll limit ({test_config.max_scroll_attempts})")

        logger.info(f"Scroll complete. Total scroll steps: {scroll_count}")

    def scroll_to_element_by_text(self, text: str, max_scrolls: int = 10) -> bool:
        """
        Scroll down until text is visible on screen.

        Args:
            text: Text to find
            max_scrolls: Maximum number of scrolls before giving up

        Returns:
            True if text was found, False if not found after max_scrolls
        """
        logger.info(f"Scrolling to find text: '{text}'")
        for _ in range(max_scrolls):
            page_source = self.driver.page_source
            if text.lower() in page_source.lower():
                logger.success(f"✅ Found '{text}' on screen")
                return True
            self._perform_swipe_up()

        logger.warning(f"⚠️ Text '{text}' not found after {max_scrolls} scrolls")
        return False
