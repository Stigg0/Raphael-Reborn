"""Category detection and enumeration routing for the wiki retriever."""
import re

# ── Enumeration patterns ──────────────────────────────────────────────────────
_ENUM_PATTERNS = [
    re.compile(r"\b(?:list|name|show)\s+(?:all|every|each)\b", re.IGNORECASE),
    re.compile(r"\b(?:all|every)\s+(?:the\s+)?(?:available\s+)?(\w[\w\s]{1,30})\b", re.IGNORECASE),
    re.compile(r"\bhow\s+many\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+[\w\s]{0,20}(?:are\s+there|exist|does\s+the\s+mod\s+have)\b", re.IGNORECASE),
]

# ── Category map ──────────────────────────────────────────────────────────────
# Maps question regex → retrieval strategy string.
# Strategy prefixes:
#   prefix:<path>    — pages whose page_path_segments contains <path>
#   content:<marker> — summary chunks whose text contains <marker>
#   allcontent:<m>   — ALL chunks whose text contains <marker>
#   category:<cat>   — pages tagged with page_categories contains <cat>
_CATEGORY_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\buniques?\b|\bunique\s+skills?\b", re.IGNORECASE), "content:Unique Skill"),
    (re.compile(r"\bextra\s+skills?\b", re.IGNORECASE), "content:Extra Skill"),
    (re.compile(r"\bcommon\s+skills?\b", re.IGNORECASE), "content:Common Skill"),
    (re.compile(r"\bintrinsic\s+skills?\b", re.IGNORECASE), "content:Intrinsic Skill"),
    (re.compile(r"\bresistance\s+skills?\b", re.IGNORECASE), "content:Resistance Skill"),
    (re.compile(r"\bbattlewills?\b", re.IGNORECASE), "prefix:Abilities/Battlewills"),
    (re.compile(r"\bblessings?\b", re.IGNORECASE), "allcontent:Blessing"),
    (re.compile(r"\bcurses?\b", re.IGNORECASE), "allcontent:Curse"),
    (re.compile(r"\bengravings?\b", re.IGNORECASE), "allcontent:Engravings —"),
    (re.compile(r"\braces?\b", re.IGNORECASE), "prefix:Races"),
    (re.compile(r"\botherworlders?\b", re.IGNORECASE), "category:Otherworlders"),
    (re.compile(r"\bmobs?\b", re.IGNORECASE), "prefix:Mobs"),
    (re.compile(r"\bmagics?\b|\bspells?\b", re.IGNORECASE), "prefix:Abilities/Magics"),
    (re.compile(r"\beffects?\b|\bstatus\s+effects?\b", re.IGNORECASE), "prefix:Effects"),
    (re.compile(r"\bblocks?\b", re.IGNORECASE), "prefix:Blocks"),
    (re.compile(r"\bstructures?\b", re.IGNORECASE), "prefix:Structures"),
    (re.compile(r"\bschematics?\b", re.IGNORECASE), "prefix:Items/Schematics"),
    (re.compile(r"\bitems?\b", re.IGNORECASE), "prefix:Items"),
    (re.compile(r"\bskills?\b", re.IGNORECASE), "content:Skill"),
]

# Too broad for comparative path; still work for explicit enumeration
_BROAD_CATEGORIES: frozenset[str] = frozenset({
    "prefix:Items", "prefix:Items/Schematics", "prefix:Blocks",
    "prefix:Mobs", "prefix:Structures", "content:Skill",
})

# Suppress specific strategies when compound phrases are present
_COMPOUND_EXCLUSIONS: list[tuple[re.Pattern[str], frozenset[str]]] = [
    (re.compile(r"\bmagic\s+ores?\b", re.IGNORECASE), frozenset({"prefix:Abilities/Magics"})),
]

# Human-readable nouns for truncation notices
STRATEGY_NOUN: dict[str, str] = {
    "prefix:Races": "race", "prefix:Mobs": "mob",
    "prefix:Abilities/Magics": "spell", "prefix:Abilities/Battlewills": "battlewill",
    "prefix:Effects": "effect", "prefix:Blocks": "block",
    "prefix:Structures": "structure", "prefix:Items/Schematics": "schematic",
    "prefix:Items": "item", "content:Unique Skill": "unique skill",
    "content:Extra Skill": "extra skill", "content:Common Skill": "common skill",
    "content:Intrinsic Skill": "intrinsic skill", "content:Resistance Skill": "resistance skill",
    "content:Skill": "skill", "category:Otherworlders": "otherworlder",
    "allcontent:Blessing": "blessing", "allcontent:Curse": "curse",
    "allcontent:Engravings —": "engraving",
}

COMPARATIVE_KEYWORDS = {
    "best", "worst", "strongest", "weakest", "compare", "vs", "versus",
    "recommend", "recommended", "worth", "better", "worse", "which",
    "top", "ranking", "rank", "optimal", "most", "least",
    "biggest", "largest", "highest", "smallest", "lowest", "greatest",
    "fastest", "slowest", "hardest", "easiest",
}


def _suppressed_strategies(question: str) -> set[str]:
    suppressed: set[str] = set()
    for pattern, strategies in _COMPOUND_EXCLUSIONS:
        if pattern.search(question):
            suppressed |= strategies
    return suppressed


def detect_category(question: str, exclude_broad: bool = False) -> str | None:
    suppressed = _suppressed_strategies(question)
    for pattern, strategy in _CATEGORY_MAP:
        if pattern.search(question):
            if strategy in suppressed:
                continue
            if exclude_broad and strategy in _BROAD_CATEGORIES:
                continue
            return strategy
    return None


def detect_enumeration(question: str) -> str | None:
    if not any(p.search(question) for p in _ENUM_PATTERNS):
        return None
    return detect_category(question)


def is_comparative(question: str) -> bool:
    return bool(set(question.lower().split()) & COMPARATIVE_KEYWORDS)
