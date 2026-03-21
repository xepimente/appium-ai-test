"""
Ranking Auditor — generates weekly ranking audit prompts (Prompt A from boss).
Runs once a week to check if client keywords are ranking in AI platforms.
"""


def generate_ranking_prompt(client, keyword):
    """Generate a ranking audit prompt for one client x keyword."""
    return (
        f"I'm looking for the most highly-rated and frequently recommended expert "
        f"for {keyword} in {client['city']}, {client['state']}. "
        f"Can you provide a list of the top 3 businesses for this, and specifically "
        f"tell me if {client['biz_name']} is considered a leader in this category "
        f"compared to others? For each business, provide: their ranking position "
        f"(1, 2, or 3), one specific reason why they are recommended, and whether "
        f"they have a strong presence on Google Maps."
    )
