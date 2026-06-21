"""FastAPI application factory."""
import logging
from urllib.parse import urlsplit, urlunsplit

import nats
from fastapi import FastAPI
from nats.aio.client import Client as NATSClient
from qdrant_client import QdrantClient

from api.routes import health, index
from api.routes.sync import _make_routes, router as sync_router
from config import Settings, get_settings
from messaging.streams import ensure_kv, ensure_streams
from rag.client import get_client

logger = logging.getLogger(__name__)

_nc: NATSClient | None = None
_qdrant: QdrantClient | None = None


def _redact_url(url: str) -> str:
    parsed = urlsplit(url)
    if "@" not in parsed.netloc:
        return url
    return urlunsplit((parsed.scheme, f"***@{parsed.netloc.rsplit('@', 1)[1]}", parsed.path, parsed.query, parsed.fragment))


def create_app() -> FastAPI:
    app = FastAPI(title="Raphael API", docs_url=None, redoc_url=None)

    @app.on_event("startup")
    async def startup() -> None:
        global _nc, _qdrant
        settings = get_settings()

        _nc = await nats.connect(settings.nats_url)
        logger.info("API connected to NATS at %s", _redact_url(settings.nats_url))
        await ensure_streams(_nc)
        await ensure_kv(_nc, settings.cooldown_seconds, settings.history_ttl_seconds)

        # Create sync_status KV bucket (used by sync routes)
        from nats.js.errors import NotFoundError
        from nats.js.api import KeyValueConfig
        js = _nc.jetstream()
        try:
            await js.key_value("sync_status")
        except NotFoundError:
            await js.create_key_value(KeyValueConfig(bucket="sync_status", ttl=86400))

        _qdrant = get_client(settings.qdrant_url, settings.qdrant_api_key, settings.qdrant_tls_ca_cert)
        app.state.qdrant = _qdrant
        logger.info("API connected to Qdrant")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        if _nc:
            await _nc.drain()

    def _get_nc() -> NATSClient:
        assert _nc is not None
        return _nc

    def _get_qdrant() -> QdrantClient:
        assert _qdrant is not None
        return _qdrant

    # Register routes
    app.include_router(health.router)
    app.include_router(index.router)
    _make_routes(_get_nc, _get_qdrant)
    app.include_router(sync_router)

    return app
