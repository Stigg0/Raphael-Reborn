"""Subtitle retriever — queries the subtitles Qdrant collection.

Two query modes:
  persona   — filter is_raphael=True; returns Raphael's actual lines to ground the LLM persona
  lore      — no filter; returns show-context chunks relevant to the question
"""
import logging

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

from rag.embedder import embed_one

logger = logging.getLogger(__name__)

_K_PERSONA = 4
_K_LORE = 3
_PERSONA_THRESHOLD = 0.35
_LORE_THRESHOLD = 0.40


def _search(
    client: QdrantClient,
    collection: str,
    question: str,
    embed_model: str,
    k: int,
    threshold: float,
    query_filter=None,
) -> list[dict]:
    vector = embed_one(question, embed_model)
    results = client.query_points(
        collection_name=collection,
        query=vector,
        limit=k,
        query_filter=query_filter,
        with_payload=True,
    )
    chunks: list[dict] = []
    for point in results.points:
        if point.score < threshold:
            continue
        p = point.payload or {}
        chunks.append({
            "text": p.get("text", ""),
            "speaker": p.get("speaker", ""),
            "season": p.get("season", 0),
            "episode": p.get("episode", 0),
            "is_raphael": p.get("is_raphael", False),
            "score": round(point.score, 3),
        })
    return chunks


def query_persona(
    client: QdrantClient,
    collection: str,
    question: str,
    embed_model: str,
) -> list[dict]:
    """Return Raphael's actual dialogue lines most relevant to the question."""
    return _search(
        client, collection, question, embed_model, _K_PERSONA, _PERSONA_THRESHOLD,
        query_filter=Filter(must=[FieldCondition(key="is_raphael", match=MatchValue(value=True))]),
    )


def query_lore(
    client: QdrantClient,
    collection: str,
    question: str,
    embed_model: str,
) -> list[dict]:
    """Return show-context subtitle chunks relevant to the question."""
    return _search(client, collection, question, embed_model, _K_LORE, _LORE_THRESHOLD)
