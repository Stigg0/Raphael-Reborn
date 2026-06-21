"""Cross-encoder reranker — re-scores retrieved chunks by relevance to the query.

Uses cross-encoder/ms-marco-MiniLM-L-6-v2 (22 MB, CPU-friendly).
Only loaded if RERANK_MODEL is set in config (non-empty string).
Disabled silently if sentence-transformers is not available or model is empty.
"""
import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_model = None
_model_name: str | None = None


def _load_model(model_name: str):
    global _model, _model_name
    if _model is not None and _model_name == model_name:
        return _model
    with _lock:
        if _model is None or _model_name != model_name:
            try:
                from sentence_transformers import CrossEncoder
                logger.info("Loading reranker model %r...", model_name)
                _model = CrossEncoder(model_name)
                _model_name = model_name
                logger.info("Reranker loaded.")
            except Exception as exc:
                logger.warning("Failed to load reranker %r: %s — reranking disabled.", model_name, exc)
                _model = None
    return _model


def rerank(question: str, chunks: list[dict], top_k: int, model_name: str) -> list[dict]:
    """Re-score chunks against the question and return top_k in relevance order.

    Returns the original list unchanged if model_name is empty, model fails to load,
    or there are fewer chunks than top_k.
    """
    if not model_name or not chunks:
        return chunks
    if any(c.get("section") == "CurseForge project metadata" for c in chunks):
        return chunks

    model = _load_model(model_name)
    if model is None:
        return chunks

    try:
        pairs = [(question, c.get("text", "")) for c in chunks]
        scores = model.predict(pairs)
        ranked = sorted(zip(scores, chunks), key=lambda x: x[0], reverse=True)
        result = []
        for score, chunk in ranked[:top_k]:
            result.append({**chunk, "rerank_score": float(score)})
        return result
    except Exception as exc:
        logger.warning("Reranking failed: %s — returning original order.", exc)
        return chunks
