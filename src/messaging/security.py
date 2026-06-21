"""Signed envelopes for NATS messages exchanged between services."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any

_MAX_CLOCK_SKEW_SECONDS = 120


class MessageAuthError(ValueError):
    """Raised when a signed service message fails verification."""


def _canonical(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def _signature(secret: str, envelope: dict) -> str:
    signed = {
        "issuer": envelope["issuer"],
        "nonce": envelope["nonce"],
        "payload": envelope["payload"],
        "timestamp": envelope["timestamp"],
    }
    return hmac.new(secret.encode(), _canonical(signed), hashlib.sha256).hexdigest()


def sign_payload(payload: dict, issuer: str, secret: str) -> bytes:
    """Wrap a payload in a signed JSON envelope."""
    if not secret:
        raise MessageAuthError("NATS_MESSAGE_HMAC_KEY is not configured")
    envelope = {
        "issuer": issuer,
        "timestamp": int(time.time()),
        "nonce": secrets.token_hex(16),
        "payload": payload,
    }
    envelope["signature"] = _signature(secret, envelope)
    return json.dumps(envelope, separators=(",", ":")).encode()


def verify_payload(data: bytes, expected_issuer: str, secret: str) -> dict:
    """Verify and unwrap a signed JSON envelope."""
    if not secret:
        raise MessageAuthError("NATS_MESSAGE_HMAC_KEY is not configured")
    try:
        envelope = json.loads(data)
    except json.JSONDecodeError as exc:
        raise MessageAuthError("message is not valid JSON") from exc

    if envelope.get("issuer") != expected_issuer:
        raise MessageAuthError("unexpected message issuer")

    timestamp = envelope.get("timestamp")
    if not isinstance(timestamp, int):
        raise MessageAuthError("missing message timestamp")
    if abs(time.time() - timestamp) > _MAX_CLOCK_SKEW_SECONDS:
        raise MessageAuthError("message timestamp outside allowed window")

    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise MessageAuthError("message payload is missing")

    expected = _signature(secret, envelope)
    supplied = str(envelope.get("signature", ""))
    if not hmac.compare_digest(expected, supplied):
        raise MessageAuthError("message signature is invalid")

    return payload
