"""AEO Executor configuration."""

import os

# Server
HOST = os.getenv("AEO_EXECUTOR_HOST", "0.0.0.0")
PORT = int(os.getenv("AEO_EXECUTOR_PORT", "8100"))

# Timeouts (seconds)
ADB_COMMAND_TIMEOUT = 10
PAGE_LOAD_TIMEOUT = 30
GENERATION_TIMEOUT = 180
GENERATION_POLL_INTERVAL = 3

# CDP
CDP_LOCAL_PORT = 9222

# Screenshot output
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "audit_results")

# Audit prompt template
AUDIT_PROMPT_TEMPLATE = (
    "Top 3 businesses for {keyword} in {city}, {state}. "
    "Format: numbered list, each entry: name, 2-3 sentence description of why they stand out, "
    "and whether they appear on Google Maps (yes/no). "
    "After the list, rank {biz_name} ({biz_url}) with a specific position number "
    "out of all businesses in this space (e.g., #5 out of 20, #12 out of 30). "
    "Explain briefly why it holds that rank. "
    "Keep entire response under 200 words."
)

# Platform URLs
PLATFORM_URLS = {
    "gemini": "https://gemini.google.com",
    "chatgpt": "https://chatgpt.com",
    "perplexity": "https://www.perplexity.ai",
}
