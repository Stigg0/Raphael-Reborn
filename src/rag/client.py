import logging
import threading

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PayloadSchemaType,
    TextIndexParams,
    TokenizerType,
    VectorParams,
)

logger = logging.getLogger(__name__)

VECTOR_SIZE = 768   # nomic-embed-text-v1.5 output dimension (or all-MiniLM-L6-v2 = 384)

_lock = threading.Lock()
_instance: QdrantClient | None = None


def get_client(url: str, api_key: str, tls_ca_cert: str = "") -> QdrantClient:
    """Return the shared Qdrant client singleton."""
    global _instance
    if _instance is not None:
        return _instance
    with _lock:
        if _instance is None:
            kwargs = {"verify": tls_ca_cert} if tls_ca_cert else {}
            _instance = QdrantClient(
                url=url,
                api_key=api_key,
                timeout=30,
                check_compatibility=False,
                **kwargs,
            )
            logger.info("Qdrant client connected to %s", url)
    return _instance


def ensure_wiki_collection(client: QdrantClient, collection: str) -> None:
    """Create the wiki collection and payload indexes if they don't exist."""
    existing = {c.name for c in client.get_collections().collections}
    if collection in existing:
        return

    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
    )
    # Keyword indexes for fast filtered scrolls
    for field in ("chunk_type", "page_category"):
        client.create_payload_index(collection, field, PayloadSchemaType.KEYWORD)
    # Array keyword index for path prefix matching and wiki categories
    for field in ("page_path_segments", "page_categories"):
        client.create_payload_index(collection, field, PayloadSchemaType.KEYWORD)
    # Full-text index for content: strategy
    client.create_payload_index(
        collection,
        "text",
        TextIndexParams(type="text", tokenizer=TokenizerType.WORD, lowercase=True, min_token_len=2),
    )
    logger.info("Created wiki collection %r with indexes", collection)


def ensure_subtitles_collection(client: QdrantClient, collection: str) -> None:
    """Create the subtitles collection and payload indexes if they don't exist."""
    existing = {c.name for c in client.get_collections().collections}
    if collection in existing:
        return

    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
    )
    client.create_payload_index(collection, "is_raphael", PayloadSchemaType.BOOL)
    client.create_payload_index(collection, "speaker", PayloadSchemaType.KEYWORD)
    client.create_payload_index(collection, "season", PayloadSchemaType.INTEGER)
    client.create_payload_index(collection, "episode", PayloadSchemaType.INTEGER)
    logger.info("Created subtitles collection %r with indexes", collection)


def ensure_addons_collection(client: QdrantClient, collection: str) -> None:
    """Create the add-on metadata/docs collection and payload indexes."""
    existing = {c.name for c in client.get_collections().collections}
    if collection in existing:
        return

    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
    )
    for field in ("source_type", "project_title", "section"):
        client.create_payload_index(collection, field, PayloadSchemaType.KEYWORD)
    client.create_payload_index(
        collection,
        "text",
        TextIndexParams(type="text", tokenizer=TokenizerType.WORD, lowercase=True, min_token_len=2),
    )
    logger.info("Created add-ons collection %r with indexes", collection)
