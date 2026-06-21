"""Embedder — supports local sentence-transformers or an external OpenAI-compatible API.

When EMBED_PROVIDER=api, vectors are produced by calling {embed_base_url}/v1/embeddings
(LM Studio, Ollama, or any OpenAI-compatible server).  The model name sent in the
request body is EMBED_MODEL.

When EMBED_PROVIDER=local (default), sentence-transformers loads the model in-process.
"""
import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_local_instance: "SentenceTransformer | None" = None


def _get_settings():
    from config import get_settings
    return get_settings()


# ── local (sentence-transformers) ─────────────────────────────────────────────

def _get_local(model_name: str) -> "SentenceTransformer":
    global _local_instance
    if _local_instance is not None:
        return _local_instance
    with _lock:
        if _local_instance is None:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading local embedding model %r...", model_name)
            _local_instance = SentenceTransformer(model_name)
            logger.info("Embedding model loaded.")
    return _local_instance


def _embed_local(texts: list[str], model_name: str) -> list[list[float]]:
    return _get_local(model_name).encode(texts, show_progress_bar=False).tolist()


def _embed_one_local(text: str, model_name: str) -> list[float]:
    return _get_local(model_name).encode(text, show_progress_bar=False).tolist()


# ── api (OpenAI-compatible: LM Studio, Ollama, etc.) ─────────────────────────

def _embed_api(texts: list[str], model_name: str, base_url: str) -> list[list[float]]:
    import httpx
    url = base_url.rstrip("/") + "/v1/embeddings"
    resp = httpx.post(
        url,
        json={"model": model_name, "input": texts},
        timeout=60.0,
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    items = sorted(resp.json()["data"], key=lambda x: x["index"])
    return [item["embedding"] for item in items]


def _resolve_base_url(settings) -> str:
    """If embed_base_url is set use it; otherwise fall back to ollama_base_url."""
    return settings.embed_base_url or settings.ollama_base_url


# ── public API ────────────────────────────────────────────────────────────────

def embed(texts: list[str], model_name: str = "all-MiniLM-L6-v2") -> list[list[float]]:
    settings = _get_settings()
    if settings.embed_provider == "api":
        return _embed_api(texts, model_name, _resolve_base_url(settings))
    return _embed_local(texts, model_name)


def embed_one(text: str, model_name: str = "all-MiniLM-L6-v2") -> list[float]:
    settings = _get_settings()
    if settings.embed_provider == "api":
        return _embed_api([text], model_name, _resolve_base_url(settings))[0]
    return _embed_one_local(text, model_name)
