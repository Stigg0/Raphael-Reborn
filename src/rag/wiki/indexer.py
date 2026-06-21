"""Qdrant-backed wiki indexer.

Replaces the ChromaDB-based rag/indexer.py. Responsible only for
embedding and upserting chunks; scraping and cleaning live elsewhere.
"""
import logging
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
)

from rag.client import ensure_wiki_collection
from rag.embedder import embed
from rag.wiki.cleaner import build_chunks

logger = logging.getLogger(__name__)


def _point_id(page_title: str, section: str, chunk_type: str, text: str) -> str:
    """Deterministic UUID so re-indexing the same chunk is an idempotent upsert."""
    key = f"{page_title}|{section}|{chunk_type}|{text[:120]}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, key))


def delete_page_chunks(client: QdrantClient, collection: str, page_title: str) -> None:
    """Remove all stored chunks for a single wiki page."""
    client.delete(
        collection_name=collection,
        points_selector=FilterSelector(
            filter=Filter(must=[FieldCondition(key="page_title", match=MatchValue(value=page_title))])
        ),
    )
    logger.debug("Deleted chunks for %r", page_title)


def build_index(
    client: QdrantClient,
    collection: str,
    pages: list[dict],
    embed_model: str,
    batch_size: int = 64,
) -> int:
    """Embed all chunks from pages and upsert into Qdrant. Returns total chunks indexed."""
    ensure_wiki_collection(client, collection)

    all_chunks: list[dict] = []
    for page in pages:
        try:
            all_chunks.extend(build_chunks(page))
        except (ValueError, KeyError) as exc:
            logger.warning("Skipping page %r: %s", page.get("title", "?"), exc)

    if not all_chunks:
        logger.info("No chunks to index.")
        return 0

    logger.info("Indexing %d chunks...", len(all_chunks))
    total = 0
    for start in range(0, len(all_chunks), batch_size):
        batch = all_chunks[start:start + batch_size]
        texts = [c["text"] for c in batch]
        vectors = embed(texts, embed_model)
        points = [
            PointStruct(
                id=_point_id(c["page_title"], c["section"], c["chunk_type"], c["text"]),
                vector=vec,
                payload={
                    "text": c["text"],
                    "page_title": c["page_title"],
                    "section": c["section"],
                    "url": c["url"],
                    "chunk_type": c["chunk_type"],
                    "page_category": c["page_category"],
                    "page_path_segments": c["page_path_segments"],
                    "page_categories": c["page_categories"],
                },
            )
            for c, vec in zip(batch, vectors)
        ]
        client.upsert(collection_name=collection, points=points)
        total += len(batch)
        logger.info("Indexed %d/%d chunks", total, len(all_chunks))

    logger.info("Index build complete — %d chunks.", total)
    return total


def reindex_page(
    client: QdrantClient,
    collection: str,
    page: dict,
    embed_model: str,
) -> None:
    """Delete a page's old chunks and re-index the new content."""
    delete_page_chunks(client, collection, page["title"])
    build_index(client, collection, [page], embed_model)
