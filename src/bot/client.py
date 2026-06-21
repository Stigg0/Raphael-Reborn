"""Discord client factory and graceful shutdown."""
import asyncio
import logging
import os
import signal

import discord
import nats
from nats.aio.client import Client as NATSClient

from bot.events import setup_events
from bot.replies import start_reply_consumer
from config import Settings
from messaging.streams import ensure_kv, ensure_streams

logger = logging.getLogger(__name__)


async def run(settings: Settings) -> None:
    nc: NATSClient = await nats.connect(settings.nats_url)
    logger.info("Connected to NATS at %s", settings.nats_url)

    await ensure_streams(nc)
    await ensure_kv(nc, settings.cooldown_seconds, settings.history_ttl_seconds)

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    setup_events(client, nc, settings.cooldown_seconds)

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
    loop.create_task(start_reply_consumer(client, nc))

    await client.start(settings.discord_token)
