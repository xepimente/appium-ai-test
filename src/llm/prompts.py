"""
src/llm/prompts.py

Prompt templates used when asking Claude to analyze screenshots.

GOOD PROMPT ENGINEERING TIPS:
───────────────────────────────
1. Be specific about what you want extracted (not just "read this")
2. Tell Claude the format you want the output in (JSON, bullet list, etc.)
3. Give context: "This is a mobile screenshot of a Chrome browser"
4. Use negative examples: "Do NOT include browser UI elements"
"""


class Prompts:
    """All prompt templates used in this test framework."""

    # ─── Navigation Verification ──────────────────────────────────────────────

    VERIFY_PAGE_LOADED = """
This is a screenshot from Chrome browser on an Android phone.

I am trying to verify that the website '{target_url}' has loaded correctly.

Please analyze the screenshot and answer:
1. Is a webpage visible and fully loaded?
2. Does the URL or page title suggest this is '{target_url}'?
3. Is there any error message (404, connection error, etc.)?
4. What is the main heading or title of the page?

Respond in this JSON format:
{{
  "page_loaded": true/false,
  "correct_site": true/false/unknown,
  "error_detected": null or "error description",
  "page_title": "title text or null",
  "confidence": "high/medium/low",
  "notes": "any other observations"
}}
"""

    VERIFY_SEARCH_RESULTS = """
This is a screenshot from Chrome browser on an Android phone after performing a search.

The user searched for: '{search_query}'

Please analyze:
1. Are search results visible on screen?
2. Is the search query shown in the search bar?
3. What is the first/top result shown?
4. Does any result link to '{target_url}'?

Respond in JSON format:
{{
  "search_results_visible": true/false,
  "query_in_searchbar": true/false,
  "first_result_title": "title text",
  "first_result_url": "url or null",
  "target_url_visible": true/false,
  "result_count_estimate": number or null
}}
"""

    # ─── Content Extraction ───────────────────────────────────────────────────

    EXTRACT_VISIBLE_TEXT = """
This is a screenshot from Chrome browser on an Android phone.
The browser is displaying a webpage. I need to extract all readable text content.

Instructions:
- Extract ONLY the main page content (not browser chrome: URL bar, tabs, etc.)
- Include: headings, paragraphs, captions, link text, button text, list items
- Preserve the visual reading order (top to bottom, left to right)
- Mark headings with [H] prefix (e.g., "[H] About Us")
- Mark buttons/links with [LINK] prefix
- Ignore purely decorative elements

Output just the extracted text, one element per line.
If this is part {part_number} of the page, label it clearly.
"""

    EXTRACT_STRUCTURED_CONTENT = """
This is screenshot {part_number} of {total_parts} of a webpage viewed on Android Chrome.
I am reading the page from top to bottom.

Extract the visible content in this structured format:

SECTION: [name of section if identifiable]
CONTENT:
[all visible text, preserving hierarchy]

Be thorough — I need to capture everything visible in this portion of the page.
Do not include browser UI elements (address bar, navigation buttons).
"""

    # ─── Full Page Analysis ───────────────────────────────────────────────────

    FULL_PAGE_SUMMARY_SYSTEM = """You are an expert web content analyst.
You are analyzing a series of screenshots taken while scrolling through a webpage 
on an Android mobile phone (Chrome browser).
Your task is to read and summarize the complete page content from top to bottom."""

    FULL_PAGE_SUMMARY_USER = """
I have provided {screenshot_count} screenshots taken while scrolling through the 
webpage at: {target_url}

Screenshots are ordered from top of page to bottom.

Please provide:
1. **Page Overview**: What is this page about? (2-3 sentences)
2. **Main Sections**: List all major sections/headings found
3. **Key Content**: Most important information on the page
4. **Full Content Read**: A complete top-to-bottom reading of the page content
5. **Content Quality**: Is the content readable and well-structured?

Format your response clearly with these 5 sections labeled.
"""

    # ─── Error Detection ──────────────────────────────────────────────────────

    DETECT_PAGE_ERRORS = """
Analyze this Android Chrome screenshot and check for any problems:
- HTTP errors (404, 500, 403, etc.)
- SSL/Certificate warnings
- "This site can't be reached" errors
- Blank/white page (content not loaded)
- Popup dialogs blocking content
- CAPTCHA or bot verification screens
- Cookie consent banners

Report any issues found. If no errors, respond with "NO_ERRORS".
"""

    # ─── Browser State Detection ──────────────────────────────────────────────

    DETECT_BROWSER_STATE = """
This is a screenshot of Chrome browser on Android.

Identify the current state:
1. Is this the Chrome new tab page / home screen?
2. Is there a URL/address bar visible? If so, what URL is shown?
3. Is a webpage loaded, or is it showing a search engine?
4. Are any dialogs/popups visible?

Respond in JSON:
{{
  "state": "new_tab" | "loaded_page" | "search_engine" | "error" | "dialog",
  "url_shown": "url or null",
  "dialog_text": "text or null"
}}
"""

    @classmethod
    def verify_page_loaded(cls, target_url: str) -> str:
        return cls.VERIFY_PAGE_LOADED.format(target_url=target_url)

    @classmethod
    def verify_search_results(cls, search_query: str, target_url: str) -> str:
        return cls.VERIFY_SEARCH_RESULTS.format(
            search_query=search_query,
            target_url=target_url
        )

    @classmethod
    def extract_visible_text(cls, part_number: int) -> str:
        return cls.EXTRACT_VISIBLE_TEXT.format(part_number=part_number)

    @classmethod
    def extract_structured_content(cls, part_number: int, total_parts: int) -> str:
        return cls.EXTRACT_STRUCTURED_CONTENT.format(
            part_number=part_number,
            total_parts=total_parts
        )

    @classmethod
    def full_page_summary(cls, screenshot_count: int, target_url: str) -> str:
        return cls.FULL_PAGE_SUMMARY_USER.format(
            screenshot_count=screenshot_count,
            target_url=target_url
        )
