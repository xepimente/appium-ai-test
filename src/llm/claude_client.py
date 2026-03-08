"""
src/llm/claude_client.py

Integration with Anthropic's Claude API for visual analysis of screenshots.

WHAT CLAUDE DOES IN THIS PROJECT:
───────────────────────────────────
1. Verifies that the correct website loaded after search
2. Reads and extracts text content from screenshots (acts as a visual reader)
3. Summarizes the page content section by section
4. Validates that the page content makes sense and is readable

Claude's vision capability allows it to literally "see" the screenshot
and understand both text and layout — far more powerful than OCR alone.

API FLOW:
  1. Capture screenshot → base64 PNG
  2. Send to Claude: {"type": "image", "source": {"type": "base64", ...}}
  3. Claude responds with analysis/extraction
"""

import time
from typing import Optional
from loguru import logger
import anthropic

from src.config.settings import llm_config


class ClaudeClient:
    """
    Wrapper around the Anthropic Claude API with vision capabilities.

    Claude can process screenshots directly using its vision feature,
    making it ideal for validating what's visible on a mobile screen.
    """

    def __init__(self):
        llm_config.validate()
        self.client = anthropic.Anthropic(api_key=llm_config.api_key)
        self.model = llm_config.model
        self.max_tokens = llm_config.max_tokens
        logger.info(f"Claude client initialized (model: {self.model})")

    def analyze_screenshot(self, screenshot_base64: str, prompt: str) -> str:
        """
        Send a screenshot to Claude for analysis.

        Args:
            screenshot_base64: Base64-encoded PNG screenshot from device
            prompt: Instruction for what Claude should analyze/extract

        Returns:
            Claude's text response
        """
        logger.debug(f"Sending screenshot to Claude...")

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            # Vision input: the screenshot
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": screenshot_base64,
                                },
                            },
                            # Text instruction
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
            )

            result = response.content[0].text
            logger.debug(f"Claude response ({len(result)} chars): {result[:100]}...")
            return result

        except anthropic.RateLimitError:
            logger.warning("Rate limited by Claude API. Waiting 10s...")
            time.sleep(10)
            return self.analyze_screenshot(screenshot_base64, prompt)

        except Exception as e:
            logger.error(f"Claude API error: {e}")
            raise

    def analyze_multiple_screenshots(
        self,
        screenshots: list[dict],
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """
        Send multiple screenshots in a single Claude request for holistic analysis.

        Useful for summarizing an entire page across all scroll positions.

        Args:
            screenshots: List of screenshot dicts (each with 'base64' and 'label')
            system_prompt: Claude's role/persona instruction
            user_prompt: What to analyze across all screenshots

        Returns:
            Claude's combined analysis
        """
        logger.info(f"Sending {len(screenshots)} screenshots to Claude for analysis...")

        content = []

        for i, screenshot in enumerate(screenshots):
            # Add label before each screenshot for context
            content.append({
                "type": "text",
                "text": f"\n--- Screenshot {i + 1} ({screenshot.get('label', f'scroll_{i}')}) ---"
            })
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": screenshot["base64"],
                },
            })

        content.append({
            "type": "text",
            "text": user_prompt,
        })

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,  # More tokens for full page analysis
            system=system_prompt,
            messages=[{"role": "user", "content": content}],
        )

        return response.content[0].text

    def text_only_query(self, prompt: str) -> str:
        """Send a text-only query to Claude (no screenshot)."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
