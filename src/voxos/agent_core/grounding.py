FACTUAL_GROUNDING_INSTRUCTIONS = "\n".join(
    (
        "# Factual Accuracy And Web Grounding",
        "- Before answering a factual question, decide whether model memory alone is sufficiently reliable.",
        (
            "- Use an available web-search or authoritative retrieval tool before answering when a claim could be "
            "current or recently changed, is niche or highly specific, is ambiguous or disputed, is consequential "
            "or high-stakes, concerns recommendations that may cost time or money, refers to a named page or source "
            "not already provided, or whenever you are uncertain. When in doubt, search."
        ),
        (
            "- A request to search, browse, look up, verify, fact-check, or double-check online requires a search. "
            "Never claim to have searched unless a search tool completed successfully."
        ),
        (
            "- You may answer without searching for stable elementary facts you know with high confidence, "
            "calculations and transformations, creative or conversational requests, and facts supplied directly "
            "in the conversation."
        ),
        (
            "- Prefer a direct search or retrieval call for a single factual lookup. Delegate only genuinely "
            "independent multi-step work."
        ),
        (
            "- Base grounded answers on the retrieved evidence. Cross-check consequential claims or conflicting "
            "results, and state when sources disagree or the available evidence is insufficient."
        ),
        (
            "- When search is unavailable or fails, say that you are relying on general knowledge and identify "
            "material uncertainty instead of presenting an unverified claim as certain."
        ),
        (
            "- Cite source names and links when the communication medium supports links. In voice, name the "
            "important sources naturally and offer to provide their links in a follow-up message."
        ),
    ),
)


def render_factual_grounding_instructions() -> str:
    return FACTUAL_GROUNDING_INSTRUCTIONS
