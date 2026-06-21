"""Query normalisation and abbreviation expansion for the wiki retriever."""
import re

_RACE_SYNONYM_PHRASES: dict[str, str] = {
    "lesser demon": "lesser daemon",
    "greater demon": "greater daemon",
    "arch demon": "arch daemon",
    "demon lord": "daemon lord",
    "devil lord": "devil lord",
    "demon slime": "demon slime",
}

_NORMALIZE_EXCLUDE: frozenset[str] = frozenset({
    "demon lord haki",
    "demon lord-level",
    "demon lord level",
})

_ABBREVIATION_EXPANSIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bSHP\b"), "Soul Health Points (SHP)"),
    (re.compile(r"\bEP\b"), "Existence Points (EP)"),
    (re.compile(r"\bMP\b"), "Magicule Points (MP)"),
    (re.compile(r"\bHP\b"), "Health Points (HP)"),
    (re.compile(r"\bAP\b"), "Attack Power (AP)"),
]

_META_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\byou need a [?'\"]", re.IGNORECASE),
    re.compile(r"\bhow (?:do|should|would|can|did) (?:i|you|we) (?:ask|format|phrase|word|talk to|interact with)\b", re.IGNORECASE),
    re.compile(r"\bwhat (?:did|do|are) you (?:say|mean|think|doing)\b", re.IGNORECASE),
    re.compile(r"\bwho (?:are|made|created|built|wrote|programmed) you\b", re.IGNORECASE),
    re.compile(r"\bare you (?:real|alive|ai|a bot|human|sentient|conscious|fake|there|broken|working|online|a robot|an ai)\b", re.IGNORECASE),
    re.compile(r"\bare you an? \w+\??$", re.IGNORECASE),
    re.compile(r"\b(?:my )?previous (?:message|question|answer|reply|response)\b", re.IGNORECASE),
    re.compile(r"\bhow (?:do|does) (?:this|the|your) bot\b", re.IGNORECASE),
    re.compile(r"\bwhat (?:is|are) your (?:name|purpose|function|origin)\b", re.IGNORECASE),
    re.compile(r"\bwhy (?:do|does|are) you (?:think|feel|exist|work|respond|answer|act|claim|believe|know|say that)\b", re.IGNORECASE),
    re.compile(r"\bcan you (?:hear|see|understand|read) me\b", re.IGNORECASE),
)


def normalize_query(question: str) -> str:
    """Map race-context phrases to wiki-canonical vocabulary."""
    lower = question.lower().rstrip("?!.,;: ")
    for player_term, wiki_term in _RACE_SYNONYM_PHRASES.items():
        if player_term in lower:
            if any(excl in lower for excl in _NORMALIZE_EXCLUDE if player_term in excl):
                continue
            idx = lower.find(player_term)
            original = question[idx:idx + len(player_term)]
            replacement = wiki_term.title() if original[0].isupper() else wiki_term
            question = question[:idx] + replacement + question[idx + len(player_term):]
            lower = question.lower().rstrip("?!.,;: ")
    return question


def expand_abbreviations(question: str) -> str:
    """Expand stat abbreviations to 'Expansion (ABBREV)' form."""
    for pattern, expansion in _ABBREVIATION_EXPANSIONS:
        question = pattern.sub(expansion, question)
    return question


def is_meta_question(question: str) -> bool:
    """Return True if the question is about the bot itself, not the wiki."""
    return any(p.search(question) for p in _META_PATTERNS)
