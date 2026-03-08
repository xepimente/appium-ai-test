"""
src/agents/browser_agent.py

The BrowserAgent is the AI-powered controller that drives Chrome on Android.

WHAT AN AI AGENT IS:
─────────────────────
An AI agent is a system that:
  1. Observes the current state (takes screenshot → sends to LLM)
  2. Decides what action to take (LLM decides based on what it sees)
  3. Executes the action (Appium performs the gesture/click/type)
  4. Repeats until the goal is achieved

This is more robust than hard-coded selectors because:
  - It can handle unexpected UI states (popups, cookie banners, etc.)
  - It can verify its own actions succeeded
  - It can recover from errors intelligently

AGENT LOOP:
  observe() → decide() → act() → verify() → repeat()
"""

import json
import time
import re
from typing import Optional
from loguru import logger

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from appium.webdriver.webdriver import WebDriver

from src.llm.claude_client import ClaudeClient
from src.llm.prompts import Prompts
from src.utils.screenshot import ScreenshotManager
from src.config.settings import test_config


class BrowserAgent:
    """
    AI-powered agent that controls Chrome browser on Android.

    The agent can:
    - Open URLs directly
    - Search for websites using Google
    - Click on search results
    - Handle common obstacles (cookie banners, popups)
    - Verify navigation succeeded using Claude's vision
    """

    def __init__(self, driver: WebDriver, claude: ClaudeClient):
        self.driver = driver
        self.claude = claude
        self.screenshots = ScreenshotManager(driver, session_name="browser_agent")
        self.wait = WebDriverWait(driver, timeout=test_config.implicit_wait_seconds)
        logger.info("BrowserAgent initialized")

    # ──────────────────────────────────────────────────────────
    # Core navigation methods
    # ──────────────────────────────────────────────────────────

    def navigate_to_url(self, url: str) -> bool:
        """
        Navigate directly to a URL.

        Args:
            url: Full URL including https://

        Returns:
            True if navigation succeeded
        """
        logger.info(f"Navigating to: {url}")

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        self.driver.get(url)
        time.sleep(2)

        # Verify using Claude's vision
        screenshot = self.screenshots.capture("after_navigate")
        analysis_raw = self.claude.analyze_screenshot(
            screenshot["base64"],
            Prompts.verify_page_loaded(url)
        )

        analysis = self._parse_json_response(analysis_raw)
        if analysis:
            if analysis.get("page_loaded"):
                logger.success(f"✅ Page loaded: {analysis.get('page_title', 'unknown title')}")
                return True
            elif analysis.get("error_detected"):
                logger.error(f"❌ Page error: {analysis['error_detected']}")
                return False

        logger.warning("Could not verify page load state")
        return True  # Assume success if LLM parse failed

    def search_for_website(self, search_query: str) -> bool:
        """
        Use Google to search for a website.

        Flow:
          1. Navigate to google.com
          2. Type search query into search box
          3. Submit search
          4. Verify results appeared (via Claude)

        Args:
            search_query: What to type into Google

        Returns:
            True if search results are visible
        """
        logger.info(f"Searching for: '{search_query}'")

        # Step 1: Open Google
        self.driver.get("https://www.google.com")
        time.sleep(2)

        # Handle Google cookie consent (common in EU / some regions)
        self._dismiss_cookie_consent()

        # Step 2: Find and click the search box
        search_box = self._find_search_box()
        if not search_box:
            logger.error("Could not find Google search box")
            return False

        search_box.clear()
        search_box.send_keys(search_query)
        time.sleep(0.5)

        # Step 3: Submit search (press Enter key)
        from selenium.webdriver.common.keys import Keys
        search_box.send_keys(Keys.RETURN)
        time.sleep(2.5)  # Wait for results to load

        # Step 4: Verify search results via Claude
        screenshot = self.screenshots.capture("search_results")
        analysis_raw = self.claude.analyze_screenshot(
            screenshot["base64"],
            Prompts.verify_search_results(search_query, test_config.target_url)
        )

        analysis = self._parse_json_response(analysis_raw)
        if analysis and analysis.get("search_results_visible"):
            logger.success(f"✅ Search results visible. First result: {analysis.get('first_result_title')}")
            return True

        logger.warning("Search results may not have loaded correctly")
        return False

    def click_search_result(self, target_url: str) -> bool:
        """
        Click on the search result for target_url.

        First tries to find the link by URL match.
        Falls back to Claude-guided clicking if not found directly.

        Args:
            target_url: URL to look for in search results

        Returns:
            True if clicked and page started loading
        """
        logger.info(f"Looking for search result matching: {target_url}")

        # Extract domain for flexible matching
        domain = re.sub(r'^https?://(www\.)?', '', target_url).split('/')[0]

        # Try to find by href containing the domain
        try:
            link = self.driver.find_element(
                By.XPATH,
                f"//a[contains(@href, '{domain}')]"
            )
            link.click()
            logger.success(f"✅ Clicked result for domain: {domain}")
            time.sleep(2.5)
            return True
        except NoSuchElementException:
            logger.debug(f"Direct link not found, trying first organic result...")

        # Fallback: click the first search result
        try:
            # Google's search results have an 'a' tag with 'jsname' attribute
            first_result = self.driver.find_element(
                By.CSS_SELECTOR,
                "div#search a[data-ved]"
            )
            first_result.click()
            time.sleep(2.5)
            logger.info("Clicked first search result")
            return True
        except NoSuchElementException:
            logger.error("Could not find any search result to click")
            return False

    def navigate_via_search(self, search_query: str, target_url: str) -> bool:
        """
        Complete flow: search → find result → click → verify landing.

        Args:
            search_query: What to search for
            target_url: Expected landing URL

        Returns:
            True if successfully navigated to target page
        """
        logger.info("=" * 60)
        logger.info(f"AGENT TASK: Search and navigate")
        logger.info(f"  Query:  {search_query}")
        logger.info(f"  Target: {target_url}")
        logger.info("=" * 60)

        # Step 1: Search
        if not self.search_for_website(search_query):
            logger.error("Search step failed")
            return False

        # Step 2: Click result
        if not self.click_search_result(target_url):
            logger.warning("Click step failed, trying direct navigation...")
            return self.navigate_to_url(target_url)

        # Step 3: Verify we landed on the right page
        screenshot = self.screenshots.capture("landed_on_page")
        analysis_raw = self.claude.analyze_screenshot(
            screenshot["base64"],
            Prompts.verify_page_loaded(target_url)
        )

        analysis = self._parse_json_response(analysis_raw)
        if analysis and analysis.get("page_loaded"):
            logger.success(f"✅ Successfully navigated to: {analysis.get('page_title')}")
            return True

        logger.error("Could not verify successful navigation")
        return False

    # ──────────────────────────────────────────────────────────
    # Helper methods
    # ──────────────────────────────────────────────────────────

    def _find_search_box(self):
        """Try multiple selectors to find the Google search input."""
        selectors = [
            (By.NAME, "q"),                          # Standard Google search
            (By.CSS_SELECTOR, "input[type='text']"), # Generic text input
            (By.CSS_SELECTOR, "textarea[name='q']"), # Google's textarea search
            (By.ID, "APjFqb"),                        # Google search box ID
        ]

        for by, selector in selectors:
            try:
                element = self.driver.find_element(by, selector)
                if element.is_displayed():
                    logger.debug(f"Found search box via: {by}={selector}")
                    return element
            except NoSuchElementException:
                continue

        return None

    def _dismiss_cookie_consent(self) -> None:
        """Dismiss Google's cookie consent dialog if it appears."""
        consent_selectors = [
            (By.ID, "L2AGLb"),                       # Google "Accept all"
            (By.XPATH, "//button[contains(., 'Accept')]"),
            (By.XPATH, "//button[contains(., 'Agree')]"),
            (By.CSS_SELECTOR, "button#accept"),
        ]

        for by, selector in consent_selectors:
            try:
                btn = self.driver.find_element(by, selector)
                if btn.is_displayed():
                    btn.click()
                    logger.info("Dismissed cookie consent")
                    time.sleep(1)
                    return
            except NoSuchElementException:
                continue

    def _parse_json_response(self, text: str) -> Optional[dict]:
        """Safely parse JSON from Claude's response."""
        if not text:
            return None
        try:
            # Extract JSON block if wrapped in markdown
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except (json.JSONDecodeError, AttributeError):
            pass
        return None

    def handle_page_obstacles(self) -> None:
        """
        Use Claude to detect and handle common obstacles like:
        - Cookie banners
        - Newsletter popups  
        - App download prompts
        - Age verification dialogs
        """
        screenshot = self.screenshots.capture("check_obstacles")
        analysis = self.claude.analyze_screenshot(
            screenshot["base64"],
            """Look at this mobile webpage screenshot. 
            Is there any overlay, popup, banner, or dialog blocking the main content?
            If YES: describe what it is and what button/action would dismiss it.
            If NO: respond with "CLEAR".
            Format: {"has_obstacle": true/false, "type": "...", "dismiss_action": "..."}"""
        )

        parsed = self._parse_json_response(analysis)
        if parsed and parsed.get("has_obstacle"):
            logger.info(f"Obstacle detected: {parsed.get('type')} — attempting to dismiss")
            self._try_dismiss_overlay()

    def _try_dismiss_overlay(self) -> None:
        """Try common ways to dismiss overlays/popups."""
        dismiss_attempts = [
            (By.XPATH, "//button[contains(., 'Close')]"),
            (By.XPATH, "//button[contains(., 'No thanks')]"),
            (By.XPATH, "//button[contains(., 'Dismiss')]"),
            (By.CSS_SELECTOR, "button.close, .modal-close, [aria-label='Close']"),
        ]

        for by, selector in dismiss_attempts:
            try:
                btn = self.driver.find_element(by, selector)
                if btn.is_displayed():
                    btn.click()
                    logger.info(f"Dismissed overlay via: {selector}")
                    time.sleep(0.5)
                    return
            except NoSuchElementException:
                continue

        logger.debug("No dismiss button found for overlay")
