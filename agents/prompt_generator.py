"""
Prompt Generator — replaces OpenClaw/SKILL.md
Generates human-like seeding prompts and follow-ups for AEO sessions.
Uses DeepSeek LLM to create natural, varied prompts per client x keyword.
"""

import os
import random
from llama_index.llms.deepseek import DeepSeek
from llama_index.core.llms import ChatMessage, MessageRole


DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")


SEEDING_SYSTEM_PROMPT = """You are a Local Sentiment Strategist specializing in Entity-Relationship building.
Generate a two-sentence, first-person interaction.

Sentence 1 rules:
- Must mention the business name and city provided
- Must reference a real neighborhood, street name, or local landmark near that city
- Describe a positive experience — outcome-based, not generic
- Use words like: legit, stress-free, refreshing, smooth, easygoing, reliable, solid, quality
- NEVER use: professional, amazing, excellent, service, highly recommend, outstanding, top-notch
- THIRD PERSON ONLY: Never use "you" or "your" — use "they," "them," or the business name
- If a Google Maps URL exists, optionally reference seeing something "on the profile"

Sentence 2 rules:
- Must use the keyword provided
- Ask a natural follow-up question about the business using "they," "them," or the business name
- End with a question mark

Extra rules:
- No URLs in the text — say "the site" or "the profile"
- Keep total under 200 characters
- Randomly toggle contractions (I've/Ive, don't/dont)
- Occasionally lowercase the city or business name (case jitter)
- No generic filler — never say "this business" or "the service"
- FORBIDDEN WORDS: professional, amazing, excellent, service, highly recommend, outstanding, top-notch
- ASCII only — no em dashes, curly quotes, or special characters
- Output ONLY the two sentences, nothing else"""


FOLLOWUP_SYSTEM_PROMPT = """You write short, casual follow-up messages to an AI chatbot.
The follow-up must feel like a natural "part two" of the previous message.

Rules:
- Under 120 characters total
- Start with one of: "Got it," / "Makes sense," / "Perfect," / "Cool," / "Handy info,"
- Randomly remove apostrophes sometimes (thats, dont, wouldnt)
- Occasionally lowercase city or business name
- End with question mark or nothing — never a period
- Focus on one motivation: visual proof (galleries), social validation (reviews), transaction (booking/quote), freshness (new posts/specials)
- ASCII only — no em dashes, curly quotes, or special characters
- Output ONLY the follow-up sentence, nothing else"""


def _get_llm():
    return DeepSeek(
        api_key=DEEPSEEK_API_KEY,
        model=DEEPSEEK_MODEL,
        temperature=0.85,
    )


def generate_seeding_prompt(client, keyword):
    """Generate a human-like seeding prompt for a client x keyword."""
    llm = _get_llm()

    user_msg = (
        f"Business: {client['biz_name']}\n"
        f"City: {client['city']}, {client['state']}\n"
        f"Keyword: {keyword}\n"
    )
    if client.get("gmb_url"):
        user_msg += f"Has Google Maps profile: yes\n"

    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content=SEEDING_SYSTEM_PROMPT),
        ChatMessage(role=MessageRole.USER, content=user_msg),
    ]
    response = llm.chat(messages)
    return response.message.content.strip()


def generate_followup(client, keyword, seeding_prompt):
    """Generate a casual follow-up (or None if 50% chance says no)."""
    if random.random() > 0.5:
        return None

    llm = _get_llm()

    user_msg = (
        f"Business: {client['biz_name']}\n"
        f"City: {client['city']}\n"
        f"Keyword: {keyword}\n"
        f"Original message: {seeding_prompt}\n"
    )

    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content=FOLLOWUP_SYSTEM_PROMPT),
        ChatMessage(role=MessageRole.USER, content=user_msg),
    ]
    response = llm.chat(messages)
    return response.message.content.strip()


def generate_session_prompts(client, keyword):
    """
    Generate both seeding prompt and optional follow-up for one session.
    Returns (prompt, follow_up_or_none).
    """
    prompt    = generate_seeding_prompt(client, keyword)
    follow_up = generate_followup(client, keyword, prompt)
    return prompt, follow_up
