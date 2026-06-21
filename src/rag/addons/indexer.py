"""Index external add-on documentation into Qdrant."""
import logging
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from rag.client import ensure_addons_collection
from rag.embedder import embed

logger = logging.getLogger(__name__)

MAX_CHUNK_CHARS = 1400
MIN_CHUNK_CHARS = 40


def _point_id(url: str, section: str, text: str) -> str:
    key = f"{url}|{section}|{text[:120]}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, key))


def _split_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    for para in text.split("\n"):
        if len(current) + len(para) + 1 <= max_chars:
            current = (current + "\n" + para).strip() if current else para
        else:
            if current:
                chunks.append(current)
            current = para[:max_chars]
    if current:
        chunks.append(current)
    return chunks


def build_chunks(records: list[dict]) -> list[dict]:
    chunks: list[dict] = []
    for record in records:
        sections = record.get("sections") or [{"section": "Overview", "text": record.get("text", "")}]
        for section in sections:
            section_name = section.get("section", "Overview")
            text = section.get("text", "").strip()
            for part in _split_text(text):
                if len(part) < MIN_CHUNK_CHARS:
                    continue
                chunks.append({
                    "text": part,
                    "project_title": record.get("project_title", ""),
                    "section": section_name,
                    "url": record.get("url", ""),
                    "source_type": record.get("source_type", "external"),
                })
    return chunks


def build_addons_index(
    client: QdrantClient,
    collection: str,
    records: list[dict],
    embed_model: str,
    batch_size: int = 64,
    reset: bool = False,
) -> int:
    """Embed add-on source records and upsert into Qdrant."""
    ensure_addons_collection(client, collection)
    if reset:
        client.delete_collection(collection)
        ensure_addons_collection(client, collection)
        logger.info("Reset add-ons collection %r", collection)

    chunks = build_chunks(records)
    if not chunks:
        logger.info("No add-on chunks to index.")
        return 0

    total = 0
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        vectors = embed([c["text"] for c in batch], embed_model)
        points = [
            PointStruct(
                id=_point_id(c["url"], c["section"], c["text"]),
                vector=vec,
                payload={
                    "text": c["text"],
                    "project_title": c["project_title"],
                    "section": c["section"],
                    "url": c["url"],
                    "source_type": c["source_type"],
                },
            )
            for c, vec in zip(batch, vectors)
        ]
        client.upsert(collection_name=collection, points=points)
        total += len(batch)
        logger.info("Indexed %d/%d add-on chunks", total, len(chunks))

    logger.info("Add-ons index complete — %d chunks.", total)
    return total
