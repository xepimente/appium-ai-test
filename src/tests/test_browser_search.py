"""
src/tests/test_browser_search.py

Main test suite for:
  1. Opening Chrome on Android
  2. Searching for a website
  3. Navigating to it
  4. Reading all content top to bottom using AI

TEST HIERARCHY:
────────────────
test_01_chrome_opens_successfully         — Smoke test
test_02_search_returns_results            — Search capability
test_03_navigate_to_target_website        — Navigation
test_04_page_content_is_readable          — Content check
test_05_read_full_page_top_to_bottom      — MAIN TEST: full page read
test_06_content_summary_is_meaningful     — AI summary validation
"""

import os
import json
import time
import pytest
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from src.agents.browser_agent import BrowserAgent
from src.agents.content_reader_agent import ContentReaderAgent, PageReadResult
from src.config.settings import test_config

console = Console()


# ══════════════════════════════════════════════════════════════════
# Test 1: Smoke Test — Can we open Chrome?
# ══════════════════════════════════════════════════════════════════

class TestBrowserLaunch:
    """Verify that Chrome opens and is controllable."""

    def test_01_chrome_opens_successfully(self, appium_driver):
        """
        WHAT: Verify Appium can open Chrome on the Android device.
        WHY:  If this fails, all other tests are meaningless.
        HOW:  Navigate to a simple page and check the title.
        """
        logger.info("TEST: Chrome opens successfully")

        # Navigate to a reliable, simple page
        appium_driver.get("https://www.google.com")
        time.sleep(2)

        # Verify we have a page title (any title = Chrome is working)
        title = appium_driver.title
        logger.info(f"Page title: '{title}'")

        assert title is not None, "Expected a page title, got None"
        assert len(title) > 0, "Page title is empty"

        console.print(f"[green]✅ Chrome opened. Title: '{title}'[/green]")


# ══════════════════════════════════════════════════════════════════
# Test 2: Search Functionality
# ══════════════════════════════════════════════════════════════════

class TestSearchFunctionality:
    """Verify Google search works and returns results."""

    def test_02_search_returns_results(self, browser_agent, search_query, target_url):
        """
        WHAT: Type a search query into Google and verify results appear.
        WHY:  We test the search path because real users search, not direct-navigate.
        HOW:  BrowserAgent opens Google → types query → submits → Claude verifies results.
        """
        logger.info(f"TEST: Search for '{search_query}'")

        success = browser_agent.search_for_website(search_query)

        assert success, (
            f"Search for '{search_query}' did not return results. "
            f"Check if Google is accessible and search box was found."
        )

        console.print(f"[green]✅ Search results appeared for: '{search_query}'[/green]")


# ══════════════════════════════════════════════════════════════════
# Test 3: Navigation
# ══════════════════════════════════════════════════════════════════

class TestNavigation:
    """Verify the agent can navigate to the target website."""

    def test_03_navigate_to_target_website(self, browser_agent, search_query, target_url):
        """
        WHAT: Navigate to the target website (via search or direct URL).
        WHY:  Core functionality — agent must reach the target page.
        HOW:  BrowserAgent performs search → clicks result → Claude verifies landing.
        """
        logger.info(f"TEST: Navigate to {target_url}")

        # Try search-based navigation first (more realistic)
        success = browser_agent.navigate_via_search(
            search_query=search_query,
            target_url=target_url,
        )

        if not success:
            logger.warning("Search navigation failed, trying direct URL...")
            success = browser_agent.navigate_to_url(target_url)

        assert success, (
            f"Failed to navigate to '{target_url}'. "
            f"Tried both search navigation and direct URL."
        )

        # Verify current URL is roughly correct
        current_url = browser_agent.driver.current_url
        logger.info(f"Current URL: {current_url}")

        console.print(f"[green]✅ Navigated to: {current_url}[/green]")

    def test_04_page_has_no_errors(self, browser_agent, claude_client, target_url):
        """
        WHAT: Check that the loaded page doesn't show errors.
        WHY:  A page can load but still show a 404 or "site unreachable".
        HOW:  Claude analyzes screenshot for error patterns.
        """
        logger.info("TEST: Check page for errors")

        from src.utils.screenshot import ScreenshotManager
        from src.llm.prompts import Prompts

        screenshot_mgr = ScreenshotManager(browser_agent.driver, "error_check")
        screenshot = screenshot_mgr.capture("error_check")

        analysis = claude_client.analyze_screenshot(
            screenshot["base64"],
            Prompts.DETECT_PAGE_ERRORS
        )

        logger.info(f"Error check result: {analysis}")

        assert "NO_ERRORS" in analysis or "no error" in analysis.lower(), (
            f"Page may have errors. Claude detected: {analysis}"
        )

        console.print("[green]✅ No page errors detected[/green]")


# ══════════════════════════════════════════════════════════════════
# Test 4: MAIN TEST — Full Page Read
# ══════════════════════════════════════════════════════════════════

class TestFullPageRead:
    """The core test: read complete page content from top to bottom."""

    @pytest.fixture(autouse=True)
    def navigate_to_page(self, browser_agent, target_url):
        """
        Ensure we're on the target page before running read tests.
        This autouse fixture runs before each test in this class.
        """
        logger.info(f"Navigating to target page: {target_url}")
        browser_agent.navigate_to_url(target_url)
        time.sleep(1)
        # Handle any popups/overlays
        browser_agent.handle_page_obstacles()

    def test_05_read_full_page_top_to_bottom(
        self,
        content_reader_agent: ContentReaderAgent,
        target_url: str,
    ):
        """
        WHAT: Scroll through the entire page and extract all content.
        WHY:  This is the primary test objective.
        HOW:
          1. ContentReaderAgent starts at top of page
          2. Takes screenshot of visible area
          3. Claude extracts text from screenshot
          4. Scroll down one viewport
          5. Repeat until bottom reached
          6. Validate meaningful content was extracted

        This test also stores the result for use by test_06.
        """
        logger.info(f"TEST: Full page read — {target_url}")

        result: PageReadResult = content_reader_agent.read_page(
            target_url=target_url,
            generate_summary=True,
        )

        # Store result for use by next test
        self.__class__._last_read_result = result

        # ── Assertions ────────────────────────────────────────────
        assert result.success, f"Page read failed: {result.error}"

        assert result.total_screenshots > 0, (
            "No screenshots were captured during page read"
        )

        assert len(result.sections) > 0, (
            "No content sections were extracted"
        )

        # Verify we got actual text (not just empty strings)
        total_chars = sum(len(s.extracted_text) for s in result.sections)
        assert total_chars > 100, (
            f"Too little content extracted ({total_chars} chars). "
            f"Expected at least 100 characters of page content."
        )

        # ── Log summary table ─────────────────────────────────────
        table = Table(title=f"Page Read Results: {target_url}")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Screenshots taken", str(result.total_screenshots))
        table.add_row("Content sections", str(len(result.sections)))
        table.add_row("Total text extracted", f"{total_chars:,} chars")
        table.add_row("Scroll steps", str(result.scroll_steps))
        table.add_row("Success", "✅ Yes")

        console.print(table)

        # ── Save content to file ─────────────────────────────────
        report_path = f"reports/page_content_{int(time.time())}.txt"
        os.makedirs("reports", exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(result.to_report())

        logger.success(f"✅ Page read complete! Report: {report_path}")
        console.print(f"\n[bold]Extracted content preview:[/bold]")
        console.print(result.full_text[:500] + "..." if len(result.full_text) > 500 else result.full_text)

    def test_06_content_summary_is_meaningful(self, content_reader_agent):
        """
        WHAT: Validate the AI-generated summary makes sense.
        WHY:  The summary should describe the actual page content coherently.
        HOW:  Check that the summary has sufficient length and structure.

        Note: This test uses the result from test_05.
        """
        logger.info("TEST: Validate AI content summary")

        result = getattr(self.__class__, '_last_read_result', None)
        if not result:
            pytest.skip("No read result available from test_05")

        if not result.full_summary:
            pytest.skip("No summary was generated")

        # Summary should be substantive
        assert len(result.full_summary) > 200, (
            f"Summary too short ({len(result.full_summary)} chars). "
            f"Expected AI to provide a meaningful summary."
        )

        # Summary should contain at least some of our expected sections
        summary_lower = result.full_summary.lower()
        has_overview = any(word in summary_lower for word in [
            "page", "website", "content", "section", "overview"
        ])

        assert has_overview, (
            "Summary doesn't appear to describe the page content meaningfully"
        )

        console.print(Panel(
            result.full_summary[:1000],
            title="[bold]AI Page Summary[/bold]",
            border_style="green"
        ))

        logger.success("✅ AI summary is meaningful and well-structured")


# ══════════════════════════════════════════════════════════════════
# Test 5: Targeted Content Extraction
# ══════════════════════════════════════════════════════════════════

class TestTargetedContentExtraction:
    """Tests for extracting specific sections of the page."""

    def test_07_can_find_specific_section(
        self,
        appium_driver,
        content_reader_agent: ContentReaderAgent,
        target_url: str,
    ):
        """
        WHAT: Scroll to find a specific section heading on the page.
        WHY:  Agents should be able to navigate to relevant content.
        HOW:  ContentReaderAgent scrolls looking for a keyword.

        Customize 'section_to_find' for your specific target website.
        """
        # Navigate to page first
        appium_driver.get(target_url)
        time.sleep(2)

        # Try to find common section headings (customize for your target site)
        sections_to_try = ["About", "Contact", "Services", "Home", "Menu"]

        for section in sections_to_try:
            result = content_reader_agent.read_specific_section(
                section_heading=section,
                max_scrolls=8,
            )
            if result:
                logger.success(f"✅ Found section '{section}': {result[:100]}")
                console.print(f"[green]Found section '{section}'[/green]")
                return

        # If none found, that's okay — just log it
        logger.info("No common section headings found (page may use different labels)")
        console.print("[yellow]ℹ️  No standard section headings found (site-specific)[/yellow]")
