"""NATS JetStream stream + KV definitions.

All stream/bucket names live here so bot, worker, and api share the same
constants without coupling their implementations to each other.
"""
import json
import logging

from nats.aio.client import Client as NATSClient
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    DeliverPolicy,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.errors import NotFoundError
from nats.js.kv import KeyValue

from messaging.security import sign_payload

logger = logging.getLogger(__name__)

# Server-side de-dup window for JetStream publishes. Must cover the signed
# envelope's accepted clock-skew window so a captured envelope cannot be
# re-published (replayed) inside its validity period. See messaging/security.py.
_DUPLICATE_WINDOW_SECONDS = 120

# ── Stream names ──────────────────────────────────────────────────────────────
MESSAGES_STREAM = "MESSAGES"
MESSAGES_SUBJECT = "events.messages"

REPLIES_STREAM = "REPLIES"
REPLIES_SUBJECT = "events.replies"

# ── KV bucket names ───────────────────────────────────────────────────────────
KV_RATELIMIT = "ratelimit"   # TTL = cooldown_seconds (set at creation)
KV_HISTORY = "history"       # TTL = history_ttl_seconds


async def ensure_streams(nc: NATSClient) -> None:
    """Create JetStream streams and KV buckets if they do not exist."""
    js = nc.jetstream()

    stream_defs: list[tuple[str, list[str]]] = [
        (MESSAGES_STREAM, [MESSAGES_SUBJECT]),
        (REPLIES_STREAM, [REPLIES_SUBJECT]),
    ]
    for name, subjects in stream_defs:
        try:
            await js.stream_info(name)
            logger.debug("Stream %r already exists", name)
        except NotFoundError:
            await js.add_stream(
                StreamConfig(
                    name=name,
                    subjects=subjects,
                    retention=RetentionPolicy.WORK_QUEUE,
                    storage=StorageType.FILE,
                    max_age=3600,          # 1 h — unprocessed messages expire
                    duplicate_window=_DUPLICATE_WINDOW_SECONDS,
                )
            )
            logger.info("Created stream %r", name)


async def ensure_kv(nc: NATSClient, cooldown_seconds: int, history_ttl_seconds: int) -> None:
    """Create KV buckets with appropriate TTLs if they do not exist."""
    from nats.js.api import KeyValueConfig
    js = nc.jetstream()

    buckets: list[tuple[str, int]] = [
        (KV_RATELIMIT, cooldown_seconds),
        (KV_HISTORY, history_ttl_seconds),
    ]
    for bucket, ttl in buckets:
        try:
            await js.key_value(bucket)
            logger.debug("KV bucket %r already exists", bucket)
        except NotFoundError:
            await js.create_key_value(KeyValueConfig(bucket=bucket, ttl=ttl))
            logger.info("Created KV bucket %r (ttl=%ds)", bucket, ttl)


async def get_kv(nc: NATSClient, bucket: str) -> KeyValue:
    return await nc.jetstream().key_value(bucket)


async def publish_signed(
    nc: NATSClient, subject: str, payload: dict, issuer: str, secret: str
) -> None:
    """Publish a signed envelope, using its nonce as the JetStream de-dup id.

    Setting ``Nats-Msg-Id`` lets the server reject a replayed re-publish of the
    same captured envelope within ``duplicate_window``. This does not interfere
    with normal consumer redelivery, which re-delivers the already-stored message
    rather than publishing a new one.
    """
    data = sign_payload(payload, issuer, secret)
    nonce = json.loads(data)["nonce"]
    await nc.jetstream().publish(subject, data, headers={"Nats-Msg-Id": nonce})
