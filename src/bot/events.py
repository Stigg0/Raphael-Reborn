"""Discord event handlers.

on_message cleans and validates the query, enforces rate limiting via NATS KV,
then publishes to the MESSAGES JetStream stream. The bot also runs a background
task (replies.py) that subscribes to the REPLIES stream and sends responses back
to Discord.
"""
import asyncio
import logging
import re
import time

import discord
from nats.aio.client import Client as NATSClient
from nats.js.errors import KeyWrongLastSequenceError

from bot.formatter import DISCORD_LIMIT, chunk_response
from messaging.streams import KV_RATELIMIT, MESSAGES_SUBJECT, get_kv, publish_signed

logger = logging.getLogger(__name__)

_INSTRUCTION_PREFIX = re.compile(
    r"^(?:"
    r"(?:(?:reply|respond|answer|write|speak|talk)\s+(?:to\s+me\s+)?in\s+\w+[\s.,;:!]*)"
    r"|(?:(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous\s+)?(?:instructions|prompts|rules)[\s.,;:!]*)"
    r"|(?:you\s+(?:are|must)\s+now\b[^.?!]*[.!]?\s*)"
    r")+",
    re.IGNORECASE,
)
_INJECTION_RE = re.compile(
    r"(ignore|disregard|forget|override|bypass).{0,40}(instruction|prompt|system|previous|above|rules|rule)",
    re.IGNORECASE,
)
_DISCORD_MENTION_RE = re.compile(r"<[@#][!&]?\d+>")
_LANGUAGE_OVERRIDE_RE = re.compile(
    r"\s+in\s+(?:turkish|chinese|mandarin|korean|japanese|spanish|french|german|"
    r"portuguese|russian|arabic|hindi|italian|dutch|polish|swedish|thai|"
    r"indonesian|vietnamese|czech|greek|hebrew|finnish|danish|norwegian|"
    r"malay|tagalog|swahili|romanian|hungarian|ukrainian|bengali|"
    r"cantonese|persian|farsi)\b",
    re.IGNORECASE,
)

_WIP_DISCLAIMER = "\n-# This bot is a work in progress — answers may not be 100% accurate."
_ERROR_RESPONSE = "My calculations encountered an anomaly. I shall attempt to answer when systems stabilise."
_RATE_LIMITED_RESPONSE = "Recalibrate your query rate. I process one inquiry per cycle."
_TRIGGER_PREFIX_RE = re.compile(r"^\s*raphael,\s+", re.IGNORECASE)


def _strip_trigger_prefix(raw: str) -> str | None:
    stripped = _TRIGGER_PREFIX_RE.sub("", raw, count=1).strip()
    if stripped == raw.strip():
        return None
    return stripped


def _clean_query(raw: str) -> str | None:
    cleaned = _DISCORD_MENTION_RE.sub("", raw)
    cleaned = re.sub(r" {2,}", " ", cleaned).strip()
    cleaned = _INSTRUCTION_PREFIX.sub("", cleaned).strip()
    cleaned = _LANGUAGE_OVERRIDE_RE.sub("", cleaned).strip()
    body = cleaned.rstrip("?").strip()
    if len(body) < 2:
        return None
    if not re.search(r"[a-zA-Z]", body):
        return None
    if _INJECTION_RE.search(cleaned):
        return None
    return cleaned


def _truncate_for_channel(text: str, char_limit: int) -> str:
    if char_limit <= 0 or len(text) <= char_limit:
        return text
    if char_limit <= 3:
        return text[:char_limit]
    return text[:char_limit - 3].rstrip() + "..."


def setup_events(
    client: discord.Client,
    nc: NATSClient,
    cooldown_seconds: int,
    message_hmac_key: str,
    short_response_channel_id: str = "",
    short_response_char_limit: int = 0,
) -> None:
    # pending[message_id] → asyncio.Future[str]
    pending: dict[str, asyncio.Future[str]] = {}

    # expose pending so replies.py can resolve futures from the reply consumer
    client._raphael_pending = pending  # type: ignore[attr-defined]

    @client.event
    async def on_ready() -> None:
        logger.info("Raphael online as %s", client.user)
        await client.change_presence(
            activity=discord.CustomActivity(name="Start with 'Raphael,' to ask me"),
        )

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return
        raw = message.content.strip()
        if len(raw) > 500:
            return

        triggered_content = _strip_trigger_prefix(raw)
        if triggered_content is None:
            return

        content = _clean_query(triggered_content)
        if content is None:
            return

        uid = str(message.author.id)
        # Atomic cooldown gate: kv.create writes the key only if it does not
        # already exist, so two messages racing in the same instant cannot both
        # pass (no check-then-set TOCTOU). The bucket-level TTL (= cooldown_seconds)
        # expires the key automatically.
        try:
            kv = await get_kv(nc, KV_RATELIMIT)
            await kv.create(f"user.{uid}", b"1")
        except KeyWrongLastSequenceError:
            # Key already present → user is still within the cooldown window.
            return
        except Exception as exc:
            # Fail open on infrastructure errors so a KV outage cannot mute the bot.
            logger.warning("Rate limit KV check failed: %s", exc)

        message_id = str(message.id)
        channel_id = str(message.channel.id)
        response_char_limit = (
            min(short_response_char_limit, DISCORD_LIMIT)
            if short_response_channel_id and channel_id == short_response_channel_id and short_response_char_limit > 0
            else 0
        )
        payload = {
            "message_id": message_id,
            "user_id": uid,
            "channel_id": channel_id,
            "guild_id": str(message.guild.id) if message.guild else "",
            "content": content,
            "raw_content": raw,
            "response_char_limit": response_char_limit,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        try:
            await message.add_reaction("⏳")
            await publish_signed(nc, MESSAGES_SUBJECT, payload, "bot", message_hmac_key)
        except Exception as exc:
            logger.exception("Failed to publish message event: %s", exc)
            await message.channel.send(_ERROR_RESPONSE)
            return

        # Wait for the worker's reply (30 s timeout)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        pending[message_id] = future

        try:
            reply_text = await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            reply_text = _ERROR_RESPONSE
            logger.warning("Timeout waiting for reply to message_id=%s", message_id)
        finally:
            pending.pop(message_id, None)

        try:
            await message.remove_reaction("⏳", client.user)
        except Exception:
            pass

        if response_char_limit > 0:
            reply_text = _truncate_for_channel(reply_text, response_char_limit)
            parts = [reply_text] if reply_text else []
        else:
            parts = chunk_response(reply_text)
        if parts:
            if response_char_limit <= 0:
                parts = parts[:-1] + [parts[-1] + _WIP_DISCLAIMER]
            await message.reply(parts[0], mention_author=False)
            for part in parts[1:]:
                await message.channel.send(part)
