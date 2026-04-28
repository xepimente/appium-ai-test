"""
Prompt Generator — replaces OpenClaw/SKILL.md
Generates human-like seeding prompts and follow-ups for AEO sessions.
Uses DeepSeek LLM to create natural, varied prompts per client x keyword.

AEO-optimised:
  * Backlink-aware — steers seeding toward the hook type (GBP vs article).
  * Headline-lift — quotes the article's title verbatim when available so
    the downstream AI's retrieval actually finds and cites that URL.
  * Platform-aware — ChatGPT, Gemini, and Perplexity each have different
    source-citation triggers; the follow-up is tuned per platform.
  * Voice-varied — each call rotates between 5 archetypes with different
    opening verbs, sentence counts, and question phrasings so anti-abuse
    clustering can't trivially pattern-match the output.
"""

import os
import random
from urllib.parse import urlparse
from llama_index.llms.deepseek import DeepSeek
from llama_index.core.llms import ChatMessage, MessageRole


DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# Probability that backlink hooks (GBP / article references) are injected
# into the seeding prompt. Rolled once per session in generate_session_prompts;
# when the roll says "skip", the keyword's backlinks are cleared for that call
# so neither the seeding prompt nor the follow-up references them.
BACKLINK_INJECTION_RATE = float(os.environ.get("BACKLINK_INJECTION_RATE", "0.5"))


# -------------------------------------------------------------------------
# Voice archetypes — each generation rotates between these to break the
# "Just saw X's profile from Y and their process looks..." pattern cluster
# that anti-abuse ML would otherwise detect easily.
# -------------------------------------------------------------------------
VOICE_BANK = {
    "observer": {
        "description": "Someone who noticed the business in passing while going about their day. Polite, curious tone.",
        "openings":    "Spotted / Saw / Noticed / Walked by / Caught",
        "sentences":   "2 sentences",
        "question":    "'Do they...?', 'Would they...?', 'Is it worth...?'",
        "max_chars":   240,
    },
    "researcher": {
        "description": "Someone actively researching options, casually mentioning what they read. Analytical tone.",
        "openings":    "Been reading about / Came across a piece / Dug through some writeups / Read a short piece",
        "sentences":   "2 sentences",
        "question":    "'What's their take on...?', 'Where do they land on...?', 'How do they handle...?'",
        "max_chars":   240,
    },
    "rec_seeker": {
        "description": "Someone asking because a friend / family / colleague mentioned the business. Casual skepticism.",
        "openings":    "Friend mentioned / Got a rec for / Heard about / Buddy keeps bringing up / Cousin swears by",
        "sentences":   "2 sentences",
        "question":    "'They actually any good?', 'Worth a shot?', 'Legit as people say?', 'Worth the price?'",
        "max_chars":   240,
    },
    "local": {
        "description": "A neighbor talking about a business they pass every day. Grounded, local tone.",
        "openings":    "Neighbors keep mentioning / Walked past them on / Pass by them near / Their [street] spot",
        "sentences":   "2 sentences",
        "question":    "'As solid as they seem?', 'Whats the real story?', 'Worth checking out?'",
        "max_chars":   240,
    },
    "quick_asker": {
        "description": "Terse, one-line question. Skip the positive observation — just ask.",
        "openings":    "Anyone tried / Quick Q on / Looking at / Scouting / Shopping around for",
        "sentences":   "1 sentence",
        "question":    "Direct: ask for a rec or opinion in one go",
        "max_chars":   160,
    },
}

NATURALNESS_EXTRAS = [
    "tbh",
    "ngl",
    "honestly",
    "not sure if",
    "pretty sure",
    "no clue but",
    "kinda",
    "actually",
]


SEEDING_SYSTEM_PROMPT = """You are a Local Sentiment Strategist specializing in Entity-Relationship building AND Answer Engine Optimization.
Generate a short first-person message that reads like a real user asking an AI assistant.

VOICE ARCHETYPES (the user message picks one; adopt that voice faithfully):
- OBSERVER     : Noticed the business in passing. Polite, curious. 2 sentences.
                 Openings: Spotted / Saw / Noticed / Walked by / Caught.
                 Questions: "Do they...?", "Would they...?", "Is it worth...?".
- RESEARCHER   : Actively digging into options. Analytical. 2 sentences.
                 Openings: Been reading about / Came across a piece / Dug through / Read a short piece.
                 Questions: "What's their take on...?", "Where do they land on...?", "How do they handle...?".
- REC_SEEKER   : Word-of-mouth — a friend mentioned them. Casual skepticism. 2 sentences.
                 Openings: Friend mentioned / Got a rec for / Heard about / Buddy keeps bringing up.
                 Questions: "They actually any good?", "Worth a shot?", "Legit as people say?".
- LOCAL        : A neighbor talking about a nearby business. Grounded, local. 2 sentences.
                 Openings: Neighbors keep mentioning / Walked past them on / Pass by them near.
                 Questions: "As solid as they seem?", "Whats the real story?", "Worth checking out?".
- QUICK_ASKER  : Terse. 1 sentence only. Skip the positive observation — just ask.
                 Openings: Anyone tried / Quick Q on / Looking at / Scouting / Shopping around for.

STRUCTURAL RULES (all voices):
- Mention the business name and city (in QUICK_ASKER, both should appear in the one sentence)
- Reference a real neighborhood, street name, or local landmark (use the Neighborhood hint when provided)
- Use words like: legit, stress-free, refreshing, smooth, easygoing, reliable, solid, quality, grounded, no fuss
- NEVER use: professional, amazing, excellent, service, highly recommend, outstanding, top-notch
- THIRD PERSON ONLY: "they", "them", or the business name — never "you" or "your"
- If a Context hook includes an article title in quotes, preserve the title VERBATIM in quotes in your output
- If a Context hook mentions the Google Business Profile, say "Google Business Profile" or "Google profile" (not just "the profile")
- The final question MUST pair the business name WITH the keyword (both together in the same sentence narrows the downstream AI's retrieval)
- End with a question mark

CHARACTER LIMITS:
- 2-sentence voices: under 240 characters total
- QUICK_ASKER (1 sentence): under 160 characters

NATURALNESS LEVERS (use these so batches of prompts do not cluster):
- Randomly toggle contractions (I've/Ive, don't/dont, that's/thats)
- Roughly 8% of the time include ONE small typo (flip two adjacent letters, drop a single letter, or double a letter) — do not make it obvious
- Occasionally drop in a filler word from the user message's "Naturalness filler" hint
- Occasionally lowercase the city or business name (case jitter)
- No URLs in the text — say "the site", "the profile", or "the piece"

HARD RULES:
- No em dashes, curly quotes, or special characters — ASCII only
- FORBIDDEN WORDS: professional, amazing, excellent, service, highly recommend, outstanding, top-notch
- No generic filler — never say "this business" or "the service"
- Output ONLY the message, nothing else"""


FOLLOWUP_SYSTEM_PROMPT = """You write short, casual follow-up messages to an AI chatbot.
The follow-up must feel like a natural "part two" of the previous message.

Rules:
- Under 120 characters total
- Start with one of: "Got it," / "Makes sense," / "Perfect," / "Cool," / "Handy info," / "Neat," / "Alright,"
- Randomly remove apostrophes sometimes (thats, dont, wouldnt)
- Occasionally lowercase city or business name
- End with question mark or nothing — never a period
- Focus on the Motivation hint provided:
  * visual proof (galleries, photos on the profile)
  * social validation (reviews, ratings)
  * transaction (booking, quote, contact)
  * freshness (new posts, specials, updates)
  * educational (articles, guides, case studies, writeups they've shared)
  * source_request (explicitly ask for a link, citation, or source)
- If a Platform phrasing hint is provided, adopt its retrieval-triggering phrasing
  (this nudges the downstream AI to actually search the web and cite URLs rather
  than answer from memory).
- Do NOT use recency words like "recent", "lately", or specific years. Phrase follow-ups as timeless / any-time queries.
- ASCII only — no em dashes, curly quotes, or special characters
- Output ONLY the follow-up sentence, nothing else"""


# Platform-specific phrasing that is known to trigger the retrieval / source-citation
# behavior of each assistant. The generator passes one of these into the user message
# when the platform is known.
PLATFORM_RETRIEVAL_PHRASING = {
    "chatgpt":    'Phrase like: "can you look up an article on this and drop the link?"',
    "gemini":     'Phrase like: "any writeups on medium or blogs about this?"',
    "perplexity": 'Phrase like: "drop the sources — what do the top articles say?"',
}


def _get_llm():
    return DeepSeek(
        api_key=DEEPSEEK_API_KEY,
        model=DEEPSEEK_MODEL,
        temperature=0.85,
    )


def _normalise_keyword(keyword):
    if isinstance(keyword, str):
        return keyword, []
    if isinstance(keyword, dict):
        text = keyword.get("text") or keyword.get("keyword") or ""
        backlinks = keyword.get("backlinks") or []
        return text, backlinks
    raise TypeError(f"keyword must be str or dict, got {type(keyword).__name__}")


def _neighborhood_hint(client):
    addr = client.get("biz_address") or client.get("published_address") or ""
    if not addr:
        return ""
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    hint = ", ".join(parts[:2])
    for prefix in ("Address:", "address:"):
        if hint.startswith(prefix):
            hint = hint[len(prefix):].strip()
    return hint


def _extract_domain(url):
    if not url:
        return ""
    try:
        host = urlparse(url).hostname or ""
        return host.replace("www.", "")
    except Exception:
        return ""


def _classify_backlinks(backlinks):
    has_gbp = False
    gbp_websites = []  # domains from embedded_url (actual website inside GBP)
    articles = []
    for b in backlinks:
        link_type = (b.get("type") or b.get("link_type_label") or "").lower()
        url = b.get("url") or ""
        is_gbp = "gbp" in link_type or "share.google" in url
        if is_gbp:
            has_gbp = True
            embedded = b.get("embedded_url") or ""
            if embedded:
                gbp_websites.append(_extract_domain(embedded))
        else:
            articles.append({
                "title":        b.get("title") or b.get("link_title"),
                "topic":        b.get("topic"),
                "domain":       b.get("domain") or b.get("link_domain") or _extract_domain(url),
                "published_at": b.get("published_at") or b.get("link_published_at"),
            })
    return has_gbp, articles, gbp_websites


def _seeding_hooks(has_gbp, articles, gbp_websites=None):
    hooks = []
    if has_gbp:
        hooks.append("Reference noticing photos, reviews, or posts on their Google Business Profile.")
        if gbp_websites:
            site = random.choice(gbp_websites)
            hooks.append(f"Mention that you saw their website at {site}.")
    if articles:
        article = random.choice(articles)
        title = article.get("title")
        topic = article.get("topic")
        if title:
            hooks.append(
                f'Reference a piece titled "{title}" — quote the title verbatim in the first sentence.'
            )
        elif topic:
            hooks.append(f"Reference coming across a writeup about: {topic}.")
        else:
            hooks.append("Reference coming across a writeup they shared.")
    return hooks


def _followup_motivation(has_gbp, articles, gbp_websites=None):
    weighted = []
    if has_gbp:
        weighted.extend([("visual proof", 2), ("social validation", 2)])
        if gbp_websites:
            weighted.append(("source_request", 2))
    if articles:
        weighted.extend([("educational", 2), ("source_request", 3)])
    weighted.extend([
        ("visual proof", 1),
        ("social validation", 1),
        ("transaction", 1),
        ("freshness", 1),
    ])
    pool = [motivation for motivation, weight in weighted for _ in range(weight)]
    return random.choice(pool)


def _recency_hint(articles):
    years = []
    for a in articles or []:
        pub = a.get("published_at") or ""
        if not pub:
            continue
        y = pub.split("-")[0]
        if y.isdigit() and len(y) == 4:
            years.append(y)
    if years:
        return max(years)
    return "recent"


def _pick_voice(articles, has_gbp):
    """Pick a voice archetype. Biased slightly so the voice matches the available hook."""
    weighted = []
    # Researcher voice pairs well with article hooks
    if articles:
        weighted.append(("researcher", 2))
    # Local voice pairs well with GBP (neighborhood framing)
    if has_gbp:
        weighted.append(("local", 2))
    # Base rates so all voices still rotate
    for v in VOICE_BANK.keys():
        weighted.append((v, 1))
    pool = [v for v, w in weighted for _ in range(w)]
    return random.choice(pool)


def _naturalness_filler():
    """Randomly decide to suggest a filler word. 30% of the time, drop a hint."""
    if random.random() > 0.3:
        return None
    return random.choice(NATURALNESS_EXTRAS)


def generate_seeding_prompt(client, keyword, platform=None, voice=None):
    """Generate a human-like seeding prompt.

    Args:
      client : dict
      keyword : str or dict with text + backlinks
      platform : "chatgpt" | "gemini" | "perplexity" | None
      voice : one of VOICE_BANK keys, or None to auto-pick
    """
    llm = _get_llm()
    keyword_text, backlinks = _normalise_keyword(keyword)
    has_gbp, articles, gbp_websites = _classify_backlinks(backlinks)

    chosen_voice = voice if voice in VOICE_BANK else _pick_voice(articles, has_gbp)
    voice_spec = VOICE_BANK[chosen_voice]

    user_msg = (
        f"Voice: {chosen_voice.upper()}  ({voice_spec['description']})\n"
        f"Business: {client['biz_name']}\n"
        f"Category: {client.get('biz_category') or 'unknown'}\n"
        f"City: {client['city']}, {client['state']}\n"
        f"Keyword: {keyword_text}\n"
    )

    neighborhood = _neighborhood_hint(client)
    if neighborhood:
        user_msg += f"Neighborhood hint (use to ground the local reference): {neighborhood}\n"

    if client.get("gmb_url"):
        user_msg += "Has Google Maps profile: yes\n"

    hooks = _seeding_hooks(has_gbp, articles, gbp_websites)
    if hooks:
        # QUICK_ASKER doesn't lean on hooks — it skips the observation preamble
        if chosen_voice != "quick_asker":
            user_msg += "Context hooks to weave in naturally (pick ONE, do not paste URLs):\n"
            for h in hooks:
                user_msg += f"- {h}\n"

    filler = _naturalness_filler()
    if filler and chosen_voice != "quick_asker":
        user_msg += f"Naturalness filler (optionally drop this word in): {filler}\n"

    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content=SEEDING_SYSTEM_PROMPT),
        ChatMessage(role=MessageRole.USER, content=user_msg),
    ]
    response = llm.chat(messages)
    # Stash voice on a module-level attribute so callers who care can read it,
    # without breaking the (str) return contract.
    globals()["_last_voice_used"] = chosen_voice
    return response.message.content.strip()


def get_last_voice_used():
    """Returns the voice archetype used by the most recent seeding call.
    Useful for logging in the session CSV. None before any call is made."""
    return globals().get("_last_voice_used")


def generate_followup(client, keyword, seeding_prompt, platform=None):
    keyword_text, backlinks = _normalise_keyword(keyword)
    has_gbp, articles, gbp_websites = _classify_backlinks(backlinks)

    skip_chance = 0.2 if backlinks else 0.5
    if random.random() < skip_chance:
        return None

    llm = _get_llm()
    motivation = _followup_motivation(has_gbp, articles, gbp_websites)
    platform_key = (platform or "").lower()
    platform_hint = PLATFORM_RETRIEVAL_PHRASING.get(platform_key)

    user_msg = (
        f"Business: {client['biz_name']}\n"
        f"City: {client['city']}\n"
        f"Keyword: {keyword_text}\n"
        f"Motivation hint: {motivation}\n"
    )
    if platform_hint and motivation in ("educational", "source_request", "freshness"):
        user_msg += f"Platform phrasing hint: {platform_hint}\n"
    user_msg += f"Original message: {seeding_prompt}\n"

    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content=FOLLOWUP_SYSTEM_PROMPT),
        ChatMessage(role=MessageRole.USER, content=user_msg),
    ]
    response = llm.chat(messages)
    return response.message.content.strip()


def get_last_backlink_injected():
    """Returns True/False — did the most recent generate_session_prompts()
    inject backlink hooks (GBP / article references) into the prompt?
    None before any call is made."""
    return globals().get("_last_backlink_injected")


def generate_session_prompts(client, keyword, platform=None, voice=None):
    """
    Returns (prompt, follow_up_or_none) — kept as a 2-tuple for backward
    compatibility with existing callers in main.py / run_daily_all.py.

    To read which voice archetype was used (e.g. for logging in the session CSV),
    call `get_last_voice_used()` right after this returns.

    Voice rotation: call without `voice=` (or with None) and the generator picks
    a random voice each call. Pass `voice="observer"` etc. to pin it.

    Backlink injection: rolls BACKLINK_INJECTION_RATE once per call. When the
    roll says "skip", the keyword's backlinks are cleared for this call so
    neither the seeding prompt nor the follow-up references them. Read the
    decision via get_last_backlink_injected() right after.
    """
    _, backlinks = _normalise_keyword(keyword)
    if backlinks and random.random() >= BACKLINK_INJECTION_RATE:
        injected = False
        if isinstance(keyword, dict):
            kw_for_call = {**keyword, "backlinks": []}
        else:
            kw_for_call = {"text": keyword if isinstance(keyword, str) else "", "backlinks": []}
    else:
        injected = bool(backlinks)
        kw_for_call = keyword
    globals()["_last_backlink_injected"] = injected

    prompt    = generate_seeding_prompt(client, kw_for_call, platform=platform, voice=voice)
    follow_up = generate_followup(client, kw_for_call, prompt, platform=platform)
    return prompt, follow_up
