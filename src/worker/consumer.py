"""NATS JetStream pull consumer loop for the worker service."""
import asyncio
import json
import logging

import nats
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy

from config import Settings
from llm.router import LLMRouter, build_provider
from messaging.streams import MESSAGES_STREAM, MESSAGES_SUBJECT, ensure_streams
from rag.client import get_client
from worker.pipeline import process_event

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT = 5.0   # seconds to wait for next message before looping
_FETCH_BATCH = 1       # process one message at a time for predictable load


async def run(settings: Settings) -> None:
    nc = await nats.connect(settings.nats_url)
    logger.info("Worker connected to NATS at %s", settings.nats_url)

    await ensure_streams(nc)

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

    qdrant = get_client(settings.qdrant_url, settings.qdrant_api_key)
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
                event = json.loads(msg.data)
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
                )
                await msg.ack()
            except Exception:
                logger.exception("Pipeline failed for message_id=%s", event.get("message_id", "?"))
                await msg.nak()
