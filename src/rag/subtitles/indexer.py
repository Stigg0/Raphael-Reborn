"""Subtitle indexer — chunks and upserts subtitle lines into Qdrant."""
import logging
import uuid
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from rag.client import ensure_subtitles_collection
from rag.embedder import embed
from rag.subtitles.loader import load_subtitle_dir

logger = logging.getLogger(__name__)

# Chunk subtitle lines into groups of N consecutive lines for richer context
_LINES_PER_CHUNK = 4


def _chunk_lines(lines: list[dict]) -> list[dict]:
    """Group consecutive lines from the same source file into context chunks."""
    if not lines:
        return []
    chunks: list[dict] = []
    i = 0
    while i < len(lines):
        group = lines[i:i + _LINES_PER_CHUNK]
        # Only group lines from the same episode
        episode = group[0]["episode"]
        season = group[0]["season"]
        same_ep = [l for l in group if l["episode"] == episode and l["season"] == season]
        if not same_ep:
            i += 1
            continue
        speakers = list(dict.fromkeys(l["speaker"] for l in same_ep if l["speaker"]))
        text = " / ".join(f"{l['speaker']}: {l['text']}" if l["speaker"] else l["text"] for l in same_ep)
        is_raphael = any(l["is_raphael"] for l in same_ep)
        chunks.append({
            "season": season,
            "episode": episode,
            "speaker": speakers[0] if len(speakers) == 1 else "multiple",
            "text": text,
            "timestamp_start": same_ep[0]["timestamp_start"],
            "timestamp_end": same_ep[-1]["timestamp_end"],
            "is_raphael": is_raphael,
            "source_file": same_ep[0]["source_file"],
        })
        i += len(same_ep)
    return chunks


def _point_id(season: int, episode: int, timestamp_start: str, text: str) -> str:
    key = f"s{season}e{episode}|{timestamp_start}|{text[:80]}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, key))


def build_subtitle_index(
    client: QdrantClient,
    collection: str,
    subtitle_dir: Path,
    embed_model: str,
    batch_size: int = 64,
    reset: bool = False,
    raphael_only: bool = True,
) -> int:
    """Parse subtitle_dir and upsert lines into Qdrant. Returns chunk count.

    When raphael_only=True (default), only Raphael's dialogue is indexed.
    Every other character's lines are discarded before chunking.
    """
    ensure_subtitles_collection(client, collection)

    if reset:
        client.delete_collection(collection)
        ensure_subtitles_collection(client, collection)
        logger.info("Reset subtitle collection %r", collection)

    raw_lines = load_subtitle_dir(subtitle_dir)
    if not raw_lines:
        logger.warning("No subtitle lines found in %s", subtitle_dir)
        return 0

    logger.info("Loaded %d raw subtitle lines from %s", len(raw_lines), subtitle_dir)

    if raphael_only:
        raw_lines = [l for l in raw_lines if l["is_raphael"]]
        logger.info("Filtered to %d Raphael lines (raphael_only=True)", len(raw_lines))
        if not raw_lines:
            logger.warning(
                "No Raphael lines detected. Check subtitle format — ASS/SSA files with "
                "character names in the Style field work best."
            )
            return 0

    chunks = _chunk_lines(raw_lines)
    logger.info("Chunked into %d subtitle chunks", len(chunks))

    total = 0
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        texts = [c["text"] for c in batch]
        vectors = embed(texts, embed_model)
        points = [
            PointStruct(
                id=_point_id(c["season"], c["episode"], c["timestamp_start"], c["text"]),
                vector=vec,
                payload={
                    "text": c["text"],
                    "season": c["season"],
                    "episode": c["episode"],
                    "speaker": c["speaker"],
                    "timestamp_start": c["timestamp_start"],
                    "timestamp_end": c["timestamp_end"],
                    "is_raphael": c["is_raphael"],
                    "source_file": c["source_file"],
                },
            )
            for c, vec in zip(batch, vectors)
        ]
        client.upsert(collection_name=collection, points=points)
        total += len(batch)
        logger.info("Indexed %d/%d subtitle chunks", total, len(chunks))

    logger.info("Subtitle index complete — %d chunks.", total)
    return total
