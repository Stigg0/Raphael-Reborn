"""RAG + LLM pipeline — called once per Discord message event."""
import json
import logging
import time

from nats.aio.client import Client as NATSClient
from nats.js.errors import KeyNotFoundError
from qdrant_client import QdrantClient

from llm.router import LLMRouter
from messaging.streams import KV_HISTORY, REPLIES_SUBJECT, get_kv, publish_signed
from rag.addons.retriever import query as addons_query
from rag.reranker import rerank
from rag.subtitles.retriever import query_lore, query_persona
from rag.wiki.retriever import query as wiki_query
from rag.wiki.supplemental import answer_supplemental_fact

logger = logging.getLogger(__name__)

_MEMORY_SUMMARY_LEN = 120


def _coerce_response_limit(value: object) -> int:
    try:
        limit = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, limit)


def _truncate_response(text: str, char_limit: int) -> str:
    if char_limit <= 0 or len(text) <= char_limit:
        return text
    if char_limit <= 3:
        return text[:char_limit]
    return text[:char_limit - 3].rstrip() + "..."


async def _get_history(nc: NATSClient, user_id: str, max_pairs: int) -> list[tuple[str, str]]:
    try:
        kv = await get_kv(nc, KV_HISTORY)
        entry = await kv.get(f"user.{user_id}")
        pairs = json.loads(entry.value)
        return pairs[-max_pairs:]
    except KeyNotFoundError:
        return []
    except Exception as exc:
        logger.warning("Failed to load history for user %s: %s", user_id, exc)
        return []


async def _store_history(
    nc: NATSClient, user_id: str, question: str, response: str, existing: list[tuple[str, str]], max_pairs: int,
) -> None:
    try:
        kv = await get_kv(nc, KV_HISTORY)
        updated = list(existing) + [(question, response[:_MEMORY_SUMMARY_LEN])]
        updated = updated[-max_pairs:]
        await kv.put(f"user.{user_id}", json.dumps(updated).encode())
    except Exception as exc:
        logger.warning("Failed to store history for user %s: %s", user_id, exc)


async def process_event(
    event: dict,
    nc: NATSClient,
    qdrant: QdrantClient,
    router: LLMRouter,
    wiki_collection: str,
    addons_collection: str,
    subtitles_collection: str,
    embed_model: str,
    k_factual: int,
    k_comparative: int,
    relevance_threshold: float,
    max_enum_chars: int,
    max_comparative_chars: int,
    max_history_pairs: int,
    rerank_model: str = "",
    rerank_top_k: int = 5,
    message_hmac_key: str = "",
) -> None:
    """Run the full pipeline for one message event and publish the reply."""
    t_start = time.monotonic()
    message_id = event["message_id"]
    user_id = event["user_id"]
    question = event["content"]
    response_char_limit = _coerce_response_limit(event.get("response_char_limit", 0))

    history = await _get_history(nc, user_id, max_history_pairs)

    wiki_chunks = wiki_query(
        client=qdrant,
        collection=wiki_collection,
        question=question,
        k_factual=k_factual,
        k_comparative=k_comparative,
        relevance_threshold=relevance_threshold,
        embed_model=embed_model,
        max_enum_chars=max_enum_chars,
        max_comparative_chars=max_comparative_chars,
        expand_query_fn=router.expand_query,
    )

    addon_chunks = addons_query(
        client=qdrant,
        collection=addons_collection,
        question=question,
        embed_model=embed_model,
        k=rerank_top_k,
        relevance_threshold=relevance_threshold,
    )
    chunks = addon_chunks + wiki_chunks

    # Re-rank retrieved chunks so the most relevant ones go to the LLM first.
    # rerank() is a no-op when rerank_model is "" or model fails to load.
    chunks = rerank(question, chunks, top_k=rerank_top_k, model_name=rerank_model)

    supplemental_answer = answer_supplemental_fact(question, chunks)
    if supplemental_answer:
        persona_chunks = []
        response, model_used = supplemental_answer, "supplemental"
    else:
        try:
            persona_chunks = query_persona(qdrant, subtitles_collection, question, embed_model)
        except Exception:
            persona_chunks = []
        try:
            subtitle_lore_chunks = query_lore(qdrant, subtitles_collection, question, embed_model)
        except Exception:
            subtitle_lore_chunks = []

        response, model_used = router.answer(
            question=question,
            wiki_chunks=chunks,
            subtitle_persona_chunks=persona_chunks,
            subtitle_lore_chunks=subtitle_lore_chunks,
            history=history or None,
            response_char_limit=response_char_limit,
        )
    response = _truncate_response(response, response_char_limit)

    latency_ms = int((time.monotonic() - t_start) * 1000)
    logger.info(
        "Processed message_id=%s user=%s chunks=%d model=%s latency_ms=%d",
        message_id, user_id, len(chunks), model_used, latency_ms,
    )

    reply = {
        "message_id": message_id,
        "channel_id": event.get("channel_id", ""),
        "text": response,
        "model_used": model_used,
        "chunks_used": len(chunks),
        "latency_ms": latency_ms,
    }
    await publish_signed(nc, REPLIES_SUBJECT, reply, "worker", message_hmac_key)

    await _store_history(nc, user_id, question, response, history, max_history_pairs)
