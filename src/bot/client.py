"""Discord client factory and graceful shutdown."""
import asyncio
import logging
import os
import signal
from urllib.parse import urlsplit, urlunsplit

import discord
import nats
from nats.aio.client import Client as NATSClient

from bot.events import setup_events
from bot.replies import start_reply_consumer
from config import Settings

logger = logging.getLogger(__name__)


def _redact_url(url: str) -> str:
    parsed = urlsplit(url)
    if "@" not in parsed.netloc:
        return url
    return urlunsplit((parsed.scheme, f"***@{parsed.netloc.rsplit('@', 1)[1]}", parsed.path, parsed.query, parsed.fragment))


async def run(settings: Settings) -> None:
    nc: NATSClient = await nats.connect(settings.nats_url)
    logger.info("Connected to NATS at %s", _redact_url(settings.nats_url))

    intents = discord.Intents.default()
    intents.message_content = True
    # Never honour @everyone/@here/role/user mentions emitted in bot output.
    # Reply text is LLM-generated from untrusted RAG content, so a prompt
    # injection (or a crafted question) must not be able to trigger a mass ping.
    client = discord.Client(
        intents=intents,
        allowed_mentions=discord.AllowedMentions.none(),
    )

    setup_events(
        client,
        nc,
        settings.cooldown_seconds,
        settings.nats_message_hmac_key,
        settings.short_response_channel_id,
        settings.short_response_char_limit,
    )

    async def _shutdown() -> None:
        logger.info("Shutting down...")
        try:
            await client.change_presence(status=discord.Status.invisible)
        except Exception:
            pass
        await asyncio.sleep(1)
        await client.close()
        await nc.drain()
        os._exit(0)

    def _on_sigterm() -> None:
        asyncio.get_running_loop().create_task(_shutdown())

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, _on_sigterm)

    # Start reply consumer as a background task
    loop.create_task(start_reply_consumer(client, nc, settings.nats_message_hmac_key))

    await client.start(settings.discord_token)
