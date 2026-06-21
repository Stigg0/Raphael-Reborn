"""Dynamic text-contains scoring for the wiki retriever."""
import re

_DYNAMIC_TC_BASE_SCORE = 0.55
_DYNAMIC_TC_TITLE_BONUS = 0.25
_DYNAMIC_TC_SUMMARY_BONUS = 0.10
_DYNAMIC_TC_DENSITY_BONUS = 0.05
_DYNAMIC_TC_DENSITY_CAP = 0.10

_MIN_TEXT_CONTAINS_ENTITY_LEN = 4

_ENTITY_ACTION_VERBS: frozenset[str] = frozenset({
    "spawn", "summon", "get", "make", "find", "kill", "give", "craft", "build",
})

_QUESTION_STARTERS = {
    "how", "what", "why", "where", "when", "is", "are", "does",
    "can", "will", "which", "who", "whose", "whom", "do", "did",
    "has", "have", "had", "was", "were", "should", "would", "could",
    "tell", "explain", "describe", "list", "show",
}

_EXACT_BLOCKED: frozenset[str] = frozenset({
    "Abilities", "Effects", "Mobs", "Crafting", "Skills", "Magic", "Races", "Items",
})
_PREFIX_BLOCKED: tuple[str, ...] = ("Tensura: Reincarnated Wiki",)
_VERSION_PAGE_RE = re.compile(r"^\d+\.\d+")

_CONDENSATION_LIGHT = ("Type:", "HP:", "SHP:", "Obtain Cost:", "title:", "MP Range:")
_CONDENSATION_VERBOSE = (
    "Type:", "HP:", "SHP:", "Obtain Cost:", "title:", "MP Range:", "AP Range:",
    "Attack DMG:", "Attack Speed:", "Intrinsics:", "Previous:", "Next:",
    "Difficulty:", "Majin:", "Spiritual:", "Divine:", "Size:",
    "Points to Master:", "Knockback Resist:", "Speed:", "Sprint Speed:",
)


def is_blocked(page_title: str) -> bool:
    if page_title in _EXACT_BLOCKED:
        return True
    if _VERSION_PAGE_RE.match(page_title):
        return True
    return any(page_title == p or page_title.startswith(p + "/") for p in _PREFIX_BLOCKED)


def dynamic_score(page_title: str, section: str, text: str, pattern: re.Pattern[str], cap: float) -> float | None:
    """Compute a dynamic score for a text-contains hit, or None to discard."""
    title_match = bool(pattern.search(page_title))
    is_summary = section == "_summary"
    mentions = len(pattern.findall(text))
    if not title_match and not is_summary and mentions < 2:
        return None
    score = _DYNAMIC_TC_BASE_SCORE
    if title_match:
        score += _DYNAMIC_TC_TITLE_BONUS
    if is_summary:
        score += _DYNAMIC_TC_SUMMARY_BONUS
    score += min(_DYNAMIC_TC_DENSITY_CAP, (mentions - 1) * _DYNAMIC_TC_DENSITY_BONUS)
    return min(cap, score)


def condense_chunks(chunks: list[dict], verbose: bool, max_enum_chars: int, max_comparative_chars: int) -> list[dict]:
    """Trim category chunks to fit within char budget."""
    keywords = _CONDENSATION_VERBOSE if verbose else _CONDENSATION_LIGHT
    budget = max_comparative_chars if verbose else max_enum_chars
    condensed: list[dict] = []
    total = 0
    for c in chunks:
        lines = c["text"].split("\n")
        kept = [lines[0]] if lines else []
        for line in lines[1:]:
            if any(kw in line for kw in keywords):
                kept.append(line)
        entry = "\n".join(kept)
        if total + len(entry) > budget:
            break
        condensed.append({**c, "text": entry})
        total += len(entry)
    return condensed


def append_truncation_notice(condensed: list[dict], total: int, strategy: str, strategy_noun: dict[str, str]) -> list[dict]:
    dropped = total - len(condensed)
    if dropped <= 0 or not condensed:
        return condensed
    noun = strategy_noun.get(strategy, "entry")
    plural = noun + ("es" if noun.endswith(("s", "x", "ch", "sh")) else "s")
    note = (
        f"\n\n[Note: {dropped} additional {plural} omitted for length. "
        f"Ask about a specific {noun} for full details.]"
    )
    last = condensed[-1]
    return condensed[:-1] + [{**last, "text": last["text"] + note}]
