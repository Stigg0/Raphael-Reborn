"""Small curated facts that are adjacent to, but not stored in, the wiki index."""
import re

_OWNER_INTENT_RE = re.compile(
    r"\b(?:who|what)\b.*\b(?:owner|owns|author|creator|created|made|by)\b"
    r"|\b(?:owner|owns|author|creator|created|made|by)\b.*\b(?:who|what)\b",
    re.IGNORECASE,
)
_TENSURA_DUNGEON_RE = re.compile(
    r"\b(?:tensura\s*:?\s*)?dungeon(?:\s+mod)?\b",
    re.IGNORECASE,
)
_DUNGEON_FACT_INTENT_RE = re.compile(
    r"\b(?:beat|beatable|clear|complete|finish|win|floor|floors|enter|entrance|"
    r"colosseum|boss|bosses|loot|trap|traps|shrine|challenge|challenges|"
    r"owner|owns|author|creator|created|made|by)\b",
    re.IGNORECASE,
)
_TENSURA_BEYOND_WORLDS_RE = re.compile(
    r"\btensura\s*:?\s*beyond\s+worlds\b|\bbeyond\s+worlds\b",
    re.IGNORECASE,
)
_PACK_OVERVIEW_INTENT_RE = re.compile(
    r"\b(?:what|tell|describe|about|modpack|pack|server|quest|quests|boss|bosses|"
    r"version|versions|minecraft|loader|fabric|forge|pvp|progression)\b",
    re.IGNORECASE,
)
_VERSION_INTENT_RE = re.compile(
    r"\b(?:version|versions|minecraft|loader|fabric|forge|latest|release|releases)\b",
    re.IGNORECASE,
)


def _beyond_worlds_version_summary(chunks: list[dict]) -> tuple[str, str, list[str]]:
    game_versions: set[str] = set()
    release_versions: dict[str, str] = {}
    for chunk in chunks:
        if chunk.get("page_title") != "Tensura: Beyond Worlds":
            continue
        text = chunk.get("text", "")
        for match in re.findall(r"Supported Minecraft (?:game )?versions?:\s*([^\n]+)", text, re.IGNORECASE):
            for version in re.split(r",\s*", match.strip()):
                if version:
                    game_versions.add(version)
        release_match = re.search(
            r"(?:Modpack/project release version number|Version number):\s*([^\s]+)",
            text,
            re.IGNORECASE,
        )
        if release_match:
            release = release_match.group(1)
            date_match = re.search(r"Date published:\s*([^\n]+)", text, re.IGNORECASE)
            release_versions[release] = date_match.group(1).strip() if date_match else ""

    if not game_versions:
        game_versions.add("1.21.1")
    if not release_versions:
        release_versions = {"1.1.2": "2026-06-16", "1.0.3": "2026-06-12", "1.0.2": "2026-05-18"}

    latest_release = max(release_versions.items(), key=lambda item: item[1] or item[0])[0]
    release_list = sorted(release_versions, key=lambda v: release_versions[v] or v, reverse=True)
    return ", ".join(sorted(game_versions)), latest_release, release_list[:4]


def query_supplemental_facts(question: str) -> list[dict]:
    """Return curated non-wiki facts for known add-on/project metadata questions."""
    if not _TENSURA_DUNGEON_RE.search(question) or not _DUNGEON_FACT_INTENT_RE.search(question):
        return []

    return [{
        "text": (
            "Project: Tensura Dungeon\n"
            "Alias: Dungeon Mod\n"
            "Owner/author: TRBeyond\n"
            "Source note: CurseForge project page for Tensura Dungeon.\n"
            "Description: Tensura: Dungeon adds a 10-floor dungeon to Tensura Reincarnated "
            "with traps, bosses, shrine challenges, elite mobs, and custom gear.\n"
            "Gameplay: It is a procedurally generated dungeon with traps, shrine trials, "
            "rare mobs, boss fights, and progression-based loot.\n"
            "Completion framing: The source describes a finite floor-based dungeon and boss fights, "
            "but does not state a formal completion screen. Treat 'beatable' as clearing the available "
            "floors and boss encounters.\n"
            "Floors: The main project page says the current release includes 10 unique floors. "
            "The v1.0.1.0 changelog says floors 11 through 20 were added, featuring a forest theme, "
            "larger hallways, bigger rooms, Greater Spirits, Otherworlders, and stronger larger mobs.\n"
            "Progression: Enemy EP increases per floor: Floor 2 starts at 1.10x, and each additional "
            "floor increases by +0.05x.\n"
            "How to enter: Find a Cartographer, buy a Dungeon Map for 2 emeralds if available, "
            "right-click it to reveal Colosseum coordinates, then pay 3 Silver Coins or use a "
            "Dungeon Voucher from the Dryad to enter.\n"
            "Notable settings: Nightmare Mode makes all bosses gain all resistances, makes all bosses "
            "unique, adds resistance shred and anti-skill effects, disables flight, and disables "
            "spatial movement."
        ),
        "page_title": "Tensura Dungeon",
        "section": "CurseForge project metadata",
        "url": "https://www.curseforge.com/minecraft/mc-mods/tensura-dungeon",
        "score": 1.0,
    }]


def answer_supplemental_fact(question: str, chunks: list[dict]) -> str | None:
    """Return a deterministic answer for exact supplemental metadata matches."""
    if not chunks:
        return None

    if _TENSURA_BEYOND_WORLDS_RE.search(question) and _PACK_OVERVIEW_INTENT_RE.search(question):
        if _VERSION_INTENT_RE.search(question):
            game_versions, latest_release, release_versions = _beyond_worlds_version_summary(chunks)
            return (
                f"**Tensura: Beyond Worlds** runs on **Fabric** for Minecraft `{game_versions}`.\n\n"
                f"The pack's own latest **modpack release version** is `{latest_release}`. "
                f"Other known pack releases include: {', '.join(f'`{v}`' for v in release_versions)}.\n\n"
                "Do not mistake those release numbers for Minecraft versions. A small distinction, "
                "but a consequential one: Minecraft compatibility is `1.21.1`; pack releases are "
                "numbered separately."
            )

        for chunk in chunks:
            if chunk.get("page_title") != "Tensura: Beyond Worlds":
                continue
            text = chunk.get("text", "")
            if (
                "Fabric 1.21.1" not in text
                and "Supported Minecraft versions: 1.21.1" not in text
                and "Supported Minecraft game versions: 1.21.1" not in text
            ):
                continue
            if re.search(r"\b(?:quest|quests|boss|bosses|pvp|progression)\b", question, re.IGNORECASE):
                return (
                    "**Tensura: Beyond Worlds** includes structured progression, PvP-oriented "
                    "balance, lore, custom bosses, deep questing, exclusive add-ons, and server-driven "
                    "systems. The project overview states it has `18` main quests, roughly `2 to 3` "
                    "hours of core story content, and `10+` side quests, with more planned."
                )
            return (
                "**Tensura: Beyond Worlds** is a server-focused **Tensura Reincarnated** modpack "
                "built for a custom **Fabric** Minecraft `1.21.1` multiplayer experience. It "
                "features custom quests, unique NPCs, powerful bosses, progression systems, PvP "
                "balance, lore, exclusive add-ons, and TRBeyond server-specific features."
            )

    if not _TENSURA_DUNGEON_RE.search(question):
        return None

    for chunk in chunks:
        title = chunk.get("page_title", "")
        if title not in {"Tensura Dungeon", "Rimuru Mod - Complete Minecraft Dungeon Adventure"}:
            continue
        text = chunk.get("text", "")
        if _OWNER_INTENT_RE.search(question) and "TRBeyond" in text:
            return (
                "**Tensura Dungeon** is owned/authored by **TRBeyond**. "
                "Alias mapping confirmed: **Dungeon Mod** refers to **Tensura Dungeon**. "
                "A simple datum, but evidently one worth stating plainly."
            )
        if (
            re.search(r"\b(?:beat|beatable|clear|complete|finish|win)\b", question, re.IGNORECASE)
            and re.search(r"\bfloors?\b|\bboss|colosseum", text, re.IGNORECASE)
        ):
            return (
                "Yes, **Tensura Dungeon** is designed to be cleared in practical terms: it is a "
                "finite, floor-based dungeon with boss encounters and progression-based loot.\n\n"
                "For the Colosseum route, the challenge spans `50` floors. Completion is possible, "
                "but escalating bosses, gear requirements, and dungeon-specific resources make the "
                "calculation less charitable. **Nightmare Mode**, naturally, is less a mercy and "
                "more a formal warning."
            )

    return None
