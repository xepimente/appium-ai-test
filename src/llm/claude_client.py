"""
src/llm/claude_client.py

Integration with Ollama for visual analysis of screenshots using a local LLM.

WHAT THE LLM DOES IN THIS PROJECT:
───────────────────────────────────
1. Verifies that the correct website loaded after search
2. Reads and extracts text content from screenshots (acts as a visual reader)
3. Summarizes the page content section by section
4. Validates that the page content makes sense and is readable

A vision-capable Ollama model (e.g. llava:13b, moondream, minicpm-v) is required.

API FLOW:
  1. Capture screenshot → base64 PNG bytes
  2. Send to Ollama: ollama.chat(model, messages=[{images: [...], content: prompt}])
  3. Ollama responds with analysis/extraction
"""

import time
import base64
from typing import Optional
from loguru import logger
import ollama

from src.config.settings import llm_config


class ClaudeClient:
    """
    Wrapper around Ollama with vision capabilities.

    Maintains the same interface as the original ClaudeClient so no other
    code needs to change.
    """

    def __init__(self):
        llm_config.validate()
        self.client = ollama.Client(host=llm_config.host)
        self.model = llm_config.model
        self.max_tokens = llm_config.max_tokens
        logger.info(f"Ollama client initialized (host: {llm_config.host}, model: {self.model})")

    def analyze_screenshot(self, screenshot_base64: str, prompt: str) -> str:
        """
        Send a screenshot to the local LLM for analysis.

        Args:
            screenshot_base64: Base64-encoded PNG screenshot from device
            prompt: Instruction for what the LLM should analyze/extract

        Returns:
            LLM text response
        """
        logger.debug("Sending screenshot to Ollama...")

        try:
            # Ollama expects raw bytes for images
            image_bytes = base64.b64decode(screenshot_base64)

            response = self.client.chat(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [image_bytes],
                    }
                ],
                options={"num_predict": self.max_tokens},
            )

            result = response.message.content
            logger.debug(f"Ollama response ({len(result)} chars): {result[:100]}...")
            return result

        except Exception as e:
            logger.error(f"Ollama error: {e}")
            raise

    def analyze_multiple_screenshots(
        self,
        screenshots: list[dict],
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """
        Send multiple screenshots in a single request for holistic analysis.

        Useful for summarizing an entire page across all scroll positions.

        Args:
            screenshots: List of screenshot dicts (each with 'base64' and 'label')
            system_prompt: Role/persona instruction
            user_prompt: What to analyze across all screenshots

        Returns:
            Combined analysis text
        """
        logger.info(f"Sending {len(screenshots)} screenshots to Ollama for analysis...")

        # Build a single combined prompt with labels, then attach all images
        labels = "\n".join(
            f"Screenshot {i + 1}: {s.get('label', f'scroll_{i}')}"
            for i, s in enumerate(screenshots)
        )
        combined_prompt = f"{system_prompt}\n\n{labels}\n\n{user_prompt}"

        image_bytes_list = [
            base64.b64decode(s["base64"]) for s in screenshots
        ]

        response = self.client.chat(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": combined_prompt,
                    "images": image_bytes_list,
                }
            ],
            options={"num_predict": 4096},
        )

        return response.message.content

    def text_only_query(self, prompt: str) -> str:
        """Send a text-only query (no screenshot)."""
        response = self.client.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            options={"num_predict": self.max_tokens},
        )
        return response.message.content
