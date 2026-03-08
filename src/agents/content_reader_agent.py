"""
src/agents/content_reader_agent.py

The ContentReaderAgent scrolls through a webpage and uses Claude to
read and extract all visible content from top to bottom.

THIS AGENT ACTS AS A "DIGITAL READER":
────────────────────────────────────────
1. Starts at top of page
2. Takes screenshot of visible area
3. Sends screenshot to Claude: "What text do you see here?"
4. Scrolls down one viewport
5. Repeats until bottom of page
6. Combines all extracted text into a structured report

WHY USE CLAUDE INSTEAD OF DIRECT DOM EXTRACTION?
─────────────────────────────────────────────────
- JavaScript-heavy pages render differently than raw DOM
- Mobile viewport may show/hide content dynamically  
- Claude understands context, layout, and reading order
- Can handle images with text, charts with labels, etc.
- Validates content makes visual sense (not garbled text)
"""

import time
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from appium.webdriver.webdriver import WebDriver

from src.llm.claude_client import ClaudeClient
from src.llm.prompts import Prompts
from src.utils.screenshot import ScreenshotManager
from src.utils.scroll_helper import ScrollHelper
from src.config.settings import test_config

console = Console()


@dataclass
class PageSection:
    """Represents one extracted section of page content."""
    scroll_position: int
    screenshot_label: str
    screenshot_path: str
    extracted_text: str
    part_number: int


@dataclass
class PageReadResult:
    """Complete result of reading a webpage top to bottom."""
    target_url: str
    sections: list[PageSection] = field(default_factory=list)
    full_summary: Optional[str] = None
    total_screenshots: int = 0
    scroll_steps: int = 0
    success: bool = False
    error: Optional[str] = None

    @property
    def full_text(self) -> str:
        """Concatenate all extracted text sections."""
        parts = []
        for section in self.sections:
            parts.append(f"\n--- Part {section.part_number} (scroll: {section.scroll_position}px) ---")
            parts.append(section.extracted_text)
        return "\n".join(parts)

    def to_report(self) -> str:
        """Generate a human-readable report."""
        lines = [
            f"=" * 70,
            f"PAGE READ REPORT",
            f"URL: {self.target_url}",
            f"Screenshots taken: {self.total_screenshots}",
            f"Scroll steps: {self.scroll_steps}",
            f"Success: {self.success}",
            f"=" * 70,
            "",
            "── FULL CONTENT ──",
            self.full_text,
        ]

        if self.full_summary:
            lines.extend([
                "",
                "── AI SUMMARY ──",
                self.full_summary,
            ])

        return "\n".join(lines)


class ContentReaderAgent:
    """
    AI agent that reads webpage content by scrolling and analyzing screenshots.

    The agent implements the "observe → extract → scroll → repeat" loop
    until the entire page has been read.
    """

    def __init__(self, driver: WebDriver, claude: ClaudeClient):
        self.driver = driver
        self.claude = claude
        self.scroller = ScrollHelper(driver)
        self.screenshots = ScreenshotManager(driver, session_name="content_reader")
        logger.info("ContentReaderAgent initialized")

    def read_page(
        self,
        target_url: str,
        generate_summary: bool = True,
    ) -> PageReadResult:
        """
        Main method: read entire webpage content top to bottom.

        Args:
            target_url: URL of the page being read (for labeling)
            generate_summary: Whether to send all screenshots to Claude
                              for a holistic summary at the end

        Returns:
            PageReadResult with all extracted content
        """
        result = PageReadResult(target_url=target_url)
        all_screenshots = []
        part_number = 0

        console.print(Panel(
            f"[bold cyan]📖 Reading page: {target_url}[/bold cyan]\n"
            f"Max scroll attempts: {test_config.max_scroll_attempts}",
            title="ContentReaderAgent"
        ))

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("Scrolling and reading...", total=None)

                # ── Main scroll loop ──────────────────────────────────
                for scroll_position in self.scroller.scroll_page_top_to_bottom():
                    part_number += 1
                    result.scroll_steps += 1

                    progress.update(
                        task,
                        description=f"Reading part {part_number} (scroll: {scroll_position}px)..."
                    )

                    # Capture what's currently visible on screen
                    label = f"part_{part_number}_pos_{scroll_position}"
                    screenshot = self.screenshots.capture(label)
                    all_screenshots.append(screenshot)
                    result.total_screenshots += 1

                    # Ask Claude to extract text from this screenshot
                    extracted_text = self._extract_text_from_screenshot(
                        screenshot=screenshot,
                        part_number=part_number,
                        total_estimate=test_config.max_scroll_attempts,
                    )

                    # Store the section
                    section = PageSection(
                        scroll_position=scroll_position,
                        screenshot_label=label,
                        screenshot_path=screenshot["path"],
                        extracted_text=extracted_text,
                        part_number=part_number,
                    )
                    result.sections.append(section)

                    # Log a preview of what was read
                    preview = extracted_text[:100].replace('\n', ' ')
                    logger.info(f"  Part {part_number}: {preview}...")

            # ── Final summary ─────────────────────────────────────
            if generate_summary and all_screenshots:
                logger.info("Generating full page summary with Claude...")
                console.print("[cyan]Generating AI summary of entire page...[/cyan]")

                result.full_summary = self._generate_full_summary(
                    screenshots=all_screenshots,
                    target_url=target_url,
                )

            result.success = True
            console.print(f"[bold green]✅ Page read complete! ({part_number} sections captured)[/bold green]")

        except Exception as e:
            result.error = str(e)
            result.success = False
            logger.error(f"ContentReaderAgent failed: {e}")
            raise

        return result

    def read_specific_section(
        self,
        section_heading: str,
        max_scrolls: int = 15,
    ) -> Optional[str]:
        """
        Scroll until a specific section heading is found, then read it.

        Args:
            section_heading: Heading text to search for
            max_scrolls: How far to scroll looking for it

        Returns:
            Extracted text of that section, or None if not found
        """
        logger.info(f"Looking for section: '{section_heading}'")

        found = self.scroller.scroll_to_element_by_text(
            section_heading,
            max_scrolls=max_scrolls
        )

        if not found:
            return None

        # Capture the section now that it's visible
        screenshot = self.screenshots.capture(f"section_{section_heading[:20]}")
        return self._extract_text_from_screenshot(screenshot, part_number=1, total_estimate=1)

    # ──────────────────────────────────────────────────────────
    # Private helper methods
    # ──────────────────────────────────────────────────────────

    def _extract_text_from_screenshot(
        self,
        screenshot: dict,
        part_number: int,
        total_estimate: int,
    ) -> str:
        """
        Send one screenshot to Claude for text extraction.

        Claude reads the image and returns all visible text content.
        """
        prompt = Prompts.extract_structured_content(
            part_number=part_number,
            total_parts=total_estimate,
        )

        try:
            text = self.claude.analyze_screenshot(
                screenshot_base64=screenshot["base64"],
                prompt=prompt,
            )
            return text.strip()
        except Exception as e:
            logger.warning(f"Failed to extract text from screenshot: {e}")
            return f"[Extraction failed for part {part_number}: {e}]"

    def _generate_full_summary(
        self,
        screenshots: list[dict],
        target_url: str,
    ) -> str:
        """
        Send ALL screenshots to Claude for a holistic page summary.

        This gives Claude the full picture of the page rather than
        analyzing it fragment by fragment.

        Note: For very long pages, we sample screenshots to avoid
        exceeding Claude's context window.
        """
        # Sample screenshots if there are too many (keep max 10 for API limits)
        max_screenshots_for_summary = 10
        if len(screenshots) > max_screenshots_for_summary:
            step = len(screenshots) // max_screenshots_for_summary
            sampled = screenshots[::step][:max_screenshots_for_summary]
            logger.info(f"Sampling {len(sampled)}/{len(screenshots)} screenshots for summary")
        else:
            sampled = screenshots

        return self.claude.analyze_multiple_screenshots(
            screenshots=sampled,
            system_prompt=Prompts.FULL_PAGE_SUMMARY_SYSTEM,
            user_prompt=Prompts.full_page_summary(
                screenshot_count=len(sampled),
                target_url=target_url,
            ),
        )
