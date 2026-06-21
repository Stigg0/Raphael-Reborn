"""Retrieve external add-on documentation chunks from Qdrant."""
import logging
import re

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import FieldCondition, Filter, MatchText

from rag.embedder import embed_one

logger = logging.getLogger(__name__)

QUERY_PREFIX = "Tensura Minecraft add-on documentation: "
_TEXT_THRESHOLD = 0.55
_STOPWORDS = {
    "what", "who", "where", "when", "why", "how", "is", "are", "can", "the",
    "a", "an", "of", "to", "do", "does", "i", "you", "it", "mod",
    "in", "include", "includes", "included", "have", "has", "with",
}
_PACK_TERMS = {"tensura", "beyond", "worlds", "world", "modpack", "pack"}


def _chunk_from_point(point) -> dict:
    p = point.payload or {}
    return {
        "text": p.get("text", ""),
        "page_title": p.get("project_title", ""),
        "section": p.get("section", ""),
        "url": p.get("url", ""),
        "source_type": p.get("source_type", "external"),
        "score": round(point.score, 3),
    }


def _text_search(client: QdrantClient, collection: str, question: str, k: int) -> list[dict]:
    terms = []
    lower = question.lower()
    broad_project_query = bool(re.search(r"\b(?:what|tell|describe|about)\b", lower))
    version_query = bool(re.search(r"\b(?:version|versions|changelog|update|latest|minecraft|loader|fabric|forge)\b", lower))
    words = [w for w in re.findall(r"[A-Za-z0-9]+", question) if w.lower() not in _STOPWORDS and len(w) > 2]
    for size in (3, 2):
        for i in range(len(words) - size + 1):
            terms.append(" ".join(words[i:i + size]))
    terms.extend(w for w in words if w.lower() not in _PACK_TERMS)
    if "dungeon" in lower and re.search(r"\b(?:beat|beatable|clear|complete|finish|win)\b", lower):
        terms.extend(["50 challenging floors", "bosses", "Colosseum"])
    elif "dungeon" in lower:
        terms.append("Tensura Dungeon")
    if "colosseum" in lower:
        terms.append("Colosseum")
    if "enter" in lower or "entrance" in lower:
        terms.extend(["Dungeon Map", "Colosseum entrance", "3 Silver Coins"])
    if "owner" in lower or "who" in lower:
        terms.append("TRBeyond")
    if "beyond" in lower and "world" in lower:
        terms.extend(["Tensura: Beyond Worlds", "Tensura Beyond Worlds", "Project Overview"])
    if any(token in lower for token in ("loader", "fabric", "forge", "modloader")):
        terms.extend(["Loaders: fabric", "fabric"])
    if any(token in lower for token in ("minecraft", "version", "versions", "1.21.1")):
        terms.extend([
            "Supported Minecraft game versions",
            "Supported Minecraft versions",
            "Minecraft versions: 1.21.1",
            "1.21.1",
        ])
    if any(token in lower for token in ("quest", "quests", "questing")):
        terms.extend(["deep questing", "custom quests", "questing"])
    if "boss" in lower or "bosses" in lower:
        terms.extend(["custom bosses", "bosses"])
    if "pvp" in lower:
        terms.append("PvP")

    seen: dict[str, dict] = {}
    scroll_limit = max(k * 4, 20)
    for term in dict.fromkeys(terms):
        try:
            records, _ = client.scroll(
                collection_name=collection,
                scroll_filter=Filter(must=[FieldCondition(key="text", match=MatchText(text=term))]),
                limit=scroll_limit,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:
            logger.debug("Add-on text search failed for %r: %s", term, exc)
            continue
        for record in records:
            p = record.payload or {}
            text = p.get("text", "")
            section = p.get("section", "")
            title = p.get("project_title", "")
            term_pos = text.lower().find(term.lower())
            exact_score = 0.9 if term.lower() != "tensura dungeon" else 0.75
            if term_pos >= 0:
                exact_score += max(0.0, 0.08 - min(term_pos, 800) / 10000)
            if section.startswith("Bundled Mod") and term.lower() in f"{title} {section}".lower():
                exact_score += 0.25
            if section == "Project Overview":
                exact_score += 0.06
            if broad_project_query and section == "Project Overview":
                exact_score += 0.05
            if section.lower().startswith("changelog") and not version_query:
                exact_score -= 0.04
            if text not in seen:
                seen[text] = {
                    "text": text,
                    "page_title": p.get("project_title", ""),
                    "section": section,
                    "url": p.get("url", ""),
                    "source_type": p.get("source_type", "external"),
                    "score": round(exact_score, 3),
                }
    return sorted(seen.values(), key=lambda c: c["score"], reverse=True)[:k]


def query(
    client: QdrantClient,
    collection: str,
    question: str,
    embed_model: str,
    k: int = 4,
    relevance_threshold: float = _TEXT_THRESHOLD,
) -> list[dict]:
    """Search add-on documentation. Returns [] if the collection is absent."""
    try:
        vector = embed_one(QUERY_PREFIX + question, embed_model)
        results = client.query_points(
            collection_name=collection,
            query=vector,
            limit=k,
            with_payload=True,
        )
    except UnexpectedResponse as exc:
        if "Not found" in str(exc) or "doesn't exist" in str(exc):
            return []
        raise

    chunks = [_chunk_from_point(p) for p in results.points if p.score >= relevance_threshold]
    text_chunks = _text_search(client, collection, question, k)

    seen: dict[str, dict] = {}
    for chunk in text_chunks + chunks:
        text = chunk["text"]
        if text not in seen or chunk["score"] > seen[text]["score"]:
            seen[text] = chunk
    return sorted(seen.values(), key=lambda c: c["score"], reverse=True)[:k]
