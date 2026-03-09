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
import random
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
        Simulate a human finger swipe UP gesture to scroll the page DOWN.

        Mimics realistic human behaviour:
          - Slight randomness in touch position so every swipe is not identical
          - Touch-down pause before movement begins (humans hesitate briefly)
          - Movement broken into small interpolated steps so the finger visibly
            travels across the screen rather than teleporting
          - Gentle deceleration at the end of the swipe (ease-out feel)
          - Post-swipe pause lets the page momentum animation settle

        On a touchscreen:
          - Swipe UP = content moves UP = user sees content further DOWN the page
          - Start point: lower area of screen (~70% from top)
          - End point:   upper area of screen (~20% from top)
        """
        # Slight horizontal drift so swipes don't all land on the exact centre
        x_drift = random.randint(-15, 15)
        start_x = self.width // 2 + x_drift

        # Randomise start/end Y slightly so each swipe feels distinct
        start_y = int(self.height * random.uniform(0.65, 0.75))
        end_y   = int(self.height * random.uniform(0.18, 0.25))

        # Total swipe travel in pixels, broken into steps.
        # More steps = smoother, more human-like movement.
        steps = 20
        distance = start_y - end_y

        # Swipe duration: realistic human scroll is roughly 0.4–0.8 seconds of
        # finger movement.  Divide evenly across steps.
        swipe_duration = random.uniform(0.4, 0.8)
        step_pause = swipe_duration / steps

        finger = PointerInput(interaction.POINTER_TOUCH, "finger")
        actions = ActionBuilder(self.driver, mouse=finger)

        # Touch down and hold briefly before moving (human reaction time)
        actions.pointer_action.move_to_location(start_x, start_y)
        actions.pointer_action.pointer_down()
        actions.pointer_action.pause(random.uniform(0.08, 0.18))

        # Interpolate finger movement with ease-out deceleration:
        # early steps cover more distance, later steps slow down.
        for i in range(1, steps + 1):
            # Ease-out curve: progress accelerates early, slows near the end
            t = i / steps
            eased = 1 - (1 - t) ** 2          # quadratic ease-out
            current_y = int(start_y - distance * eased)
            actions.pointer_action.move_to_location(start_x, current_y)
            actions.pointer_action.pause(step_pause)

        # Lift finger
        actions.pointer_action.pointer_up()

        actions.perform()
        # Let the page momentum animation settle before the next action
        time.sleep(self.pause)

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
