"""Background task that subscribes to the REPLIES JetStream stream and
resolves pending Discord-message futures so events.py can send the reply.
"""
import asyncio
import logging

import discord
from nats.aio.client import Client as NATSClient

from messaging.security import MessageAuthError, verify_payload
from messaging.streams import REPLIES_STREAM, REPLIES_SUBJECT

logger = logging.getLogger(__name__)


async def start_reply_consumer(client: discord.Client, nc: NATSClient, message_hmac_key: str) -> None:
    """Subscribe to events.replies; resolve pending futures or log orphans."""
    js = nc.jetstream()
    sub = await js.subscribe(REPLIES_SUBJECT, durable="bot-replies", stream=REPLIES_STREAM)
    logger.info("Reply consumer started on %r", REPLIES_SUBJECT)

    async for msg in sub.messages:
        try:
            data = verify_payload(msg.data, "worker", message_hmac_key)
            await msg.ack()
        except MessageAuthError as exc:
            logger.warning("Rejected unsigned or invalid worker reply: %s", exc)
            await msg.ack()
            continue
        except Exception as exc:
            logger.warning("Failed to decode reply message: %s", exc)
            await msg.nak()
            continue

        message_id: str = data.get("message_id", "")
        reply_text: str = data.get("text", "")

        pending: dict = getattr(client, "_raphael_pending", {})
        future = pending.get(message_id)
        if future and not future.done():
            future.get_loop().call_soon_threadsafe(future.set_result, reply_text)
        else:
            logger.debug("No pending future for message_id=%s (may have timed out)", message_id)
