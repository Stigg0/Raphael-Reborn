"""NATS JetStream pull consumer loop for the worker service."""
import asyncio
import logging
from urllib.parse import urlsplit, urlunsplit

import nats
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy

from config import Settings
from llm.router import LLMRouter, build_provider
from messaging.security import MessageAuthError, verify_payload
from messaging.streams import MESSAGES_STREAM, MESSAGES_SUBJECT
from rag.client import get_client
from worker.pipeline import process_event

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT = 5.0   # seconds to wait for next message before looping
_FETCH_BATCH = 1       # process one message at a time for predictable load


def _redact_url(url: str) -> str:
    parsed = urlsplit(url)
    if "@" not in parsed.netloc:
        return url
    return urlunsplit((parsed.scheme, f"***@{parsed.netloc.rsplit('@', 1)[1]}", parsed.path, parsed.query, parsed.fragment))


async def run(settings: Settings) -> None:
    nc = await nats.connect(settings.nats_url)
    logger.info("Worker connected to NATS at %s", _redact_url(settings.nats_url))

    js = nc.jetstream()
    psub = await js.pull_subscribe(
        MESSAGES_SUBJECT,
        durable="worker",
        stream=MESSAGES_STREAM,
        config=ConsumerConfig(
            ack_policy=AckPolicy.EXPLICIT,
            deliver_policy=DeliverPolicy.ALL,
            max_deliver=3,
            ack_wait=60,
        ),
    )
    logger.info("Pull consumer ready on stream %r", MESSAGES_STREAM)

    qdrant = get_client(settings.qdrant_url, settings.qdrant_api_key, settings.qdrant_tls_ca_cert)
    provider = build_provider(
        settings.llm_provider,
        settings.groq_api_key,
        settings.ollama_base_url,
        settings.ollama_model,
        settings.openai_base_url,
        settings.openai_model,
        settings.openai_enable_web_search,
        settings.openai_web_search_max_results,
    )
    router = LLMRouter(provider)
    logger.info("LLM provider: %s", provider.model_id)

    while True:
        try:
            msgs = await psub.fetch(_FETCH_BATCH, timeout=_FETCH_TIMEOUT)
        except asyncio.TimeoutError:
            continue
        except Exception as exc:
            logger.warning("Fetch error: %s", exc)
            await asyncio.sleep(1)
            continue

        for msg in msgs:
            try:
                event = verify_payload(msg.data, "bot", settings.nats_message_hmac_key)
            except MessageAuthError as exc:
                logger.warning("Rejected unsigned or invalid bot message: %s", exc)
                await msg.ack()
                continue
            except Exception as exc:
                logger.warning("Failed to decode event: %s", exc)
                await msg.nak()
                continue

            try:
                await process_event(
                    event=event,
                    nc=nc,
                    qdrant=qdrant,
                    router=router,
                    wiki_collection=settings.wiki_collection,
                    addons_collection=settings.addons_collection,
                    subtitles_collection=settings.subtitles_collection,
                    embed_model=settings.embed_model,
                    k_factual=settings.k_factual,
                    k_comparative=settings.k_comparative,
                    relevance_threshold=settings.relevance_threshold,
                    max_enum_chars=settings.max_enum_chars,
                    max_comparative_chars=settings.max_comparative_chars,
                    max_history_pairs=settings.max_history_pairs,
                    rerank_model=settings.rerank_model,
                    rerank_top_k=settings.rerank_top_k,
                    message_hmac_key=settings.nats_message_hmac_key,
                )
                await msg.ack()
            except Exception:
                logger.exception("Pipeline failed for message_id=%s", event.get("message_id", "?"))
                await msg.nak()
