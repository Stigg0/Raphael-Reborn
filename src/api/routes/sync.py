"""Wiki and subtitle sync endpoints.

All sync operations are async: the endpoint returns 202 immediately and runs
the work in the background. Status is stored in NATS KV for polling.
"""
import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends
from nats.aio.client import Client as NATSClient
from nats.js.errors import KeyNotFoundError
from qdrant_client import QdrantClient

from auth.middleware import require_role
from config import Settings, get_settings
from messaging.streams import get_kv

logger = logging.getLogger(__name__)

router = APIRouter()

_KV_SYNC_STATUS = "sync_status"


async def _set_status(nc: NATSClient, job_id: str, status: dict) -> None:
    try:
        kv = await nc.jetstream().key_value(_KV_SYNC_STATUS)
        await kv.put(job_id, json.dumps(status).encode())
    except Exception as exc:
        logger.warning("Failed to write sync status: %s", exc)


async def _run_wiki_sync(
    job_id: str,
    incremental: bool,
    nc: NATSClient,
    qdrant: QdrantClient,
    settings: Settings,
) -> None:
    from rag.wiki.indexer import build_index, reindex_page
    from rag.wiki.scraper import (
        fetch_all_pages,
        fetch_updated_pages,
        get_last_updated,
        set_last_updated,
    )
    from rag.wiki.retriever import invalidate_title_cache

    await _set_status(nc, job_id, {"status": "running", "type": "wiki", "incremental": incremental})
    try:
        if incremental:
            since = get_last_updated()
            if since:
                pages = fetch_updated_pages(settings.wiki_host, since)
                for page in pages:
                    reindex_page(qdrant, settings.wiki_collection, page, settings.embed_model)
                count = len(pages)
            else:
                pages = fetch_all_pages(settings.wiki_host)
                count = build_index(qdrant, settings.wiki_collection, pages, settings.embed_model)
        else:
            pages = fetch_all_pages(settings.wiki_host, force_refresh=True)
            count = build_index(qdrant, settings.wiki_collection, pages, settings.embed_model)

        set_last_updated()
        invalidate_title_cache()
        await _set_status(nc, job_id, {"status": "done", "pages": count})
        logger.info("Sync job %s complete — %d pages", job_id, count)
    except Exception as exc:
        logger.exception("Sync job %s failed", job_id)
        await _set_status(nc, job_id, {"status": "error", "error": str(exc)})


async def _run_subtitle_sync(
    job_id: str,
    nc: NATSClient,
    qdrant: QdrantClient,
    settings: Settings,
) -> None:
    from rag.subtitles.indexer import build_subtitle_index

    subtitle_dir = Path("/app/subtitles")
    await _set_status(nc, job_id, {"status": "running", "type": "subtitles"})
    try:
        count = build_subtitle_index(
            qdrant, settings.subtitles_collection, subtitle_dir, settings.embed_model, reset=True,
        )
        await _set_status(nc, job_id, {"status": "done", "chunks": count})
        logger.info("Subtitle sync job %s complete — %d chunks", job_id, count)
    except Exception as exc:
        logger.exception("Subtitle sync job %s failed", job_id)
        await _set_status(nc, job_id, {"status": "error", "error": str(exc)})


def _parse_urls(raw: str) -> list[str]:
    return [url.strip() for url in raw.split(",") if url.strip()]


async def _run_addons_sync(
    job_id: str,
    nc: NATSClient,
    qdrant: QdrantClient,
    settings: Settings,
) -> None:
    from rag.addons.indexer import build_addons_index
    from rag.addons.scraper import scrape_addon_sources

    urls = _parse_urls(settings.addon_source_urls)
    await _set_status(nc, job_id, {"status": "running", "type": "addons", "sources": urls})
    try:
        records = scrape_addon_sources(
            urls,
            force_refresh=True,
            mod_wiki_max_pages=settings.addon_mod_wiki_max_pages,
        )
        count = build_addons_index(
            qdrant, settings.addons_collection, records, settings.embed_model, reset=True,
        )
        await _set_status(nc, job_id, {"status": "done", "sources": len(records), "chunks": count})
        logger.info("Add-ons sync job %s complete — %d chunks", job_id, count)
    except Exception as exc:
        logger.exception("Add-ons sync job %s failed", job_id)
        await _set_status(nc, job_id, {"status": "error", "error": str(exc)})


def _make_routes(nc_getter, qdrant_getter):
    """Create routes with injected dependencies (called from app.py)."""

    @router.post("/sync/wiki", status_code=202, dependencies=[Depends(require_role("sync"))])
    async def sync_wiki(
        background_tasks: BackgroundTasks,
        settings: Settings = Depends(get_settings),
    ) -> dict:
        job_id = str(uuid.uuid4())
        nc = nc_getter()
        qdrant = qdrant_getter()
        background_tasks.add_task(_run_wiki_sync, job_id, False, nc, qdrant, settings)
        return {"job_id": job_id, "status": "accepted"}

    @router.post("/sync/wiki/incremental", status_code=202, dependencies=[Depends(require_role("sync"))])
    async def sync_wiki_incremental(
        background_tasks: BackgroundTasks,
        settings: Settings = Depends(get_settings),
    ) -> dict:
        job_id = str(uuid.uuid4())
        nc = nc_getter()
        qdrant = qdrant_getter()
        background_tasks.add_task(_run_wiki_sync, job_id, True, nc, qdrant, settings)
        return {"job_id": job_id, "status": "accepted"}

    @router.post("/sync/subtitles", status_code=202, dependencies=[Depends(require_role("sync"))])
    async def sync_subtitles(
        background_tasks: BackgroundTasks,
        settings: Settings = Depends(get_settings),
    ) -> dict:
        job_id = str(uuid.uuid4())
        nc = nc_getter()
        qdrant = qdrant_getter()
        background_tasks.add_task(_run_subtitle_sync, job_id, nc, qdrant, settings)
        return {"job_id": job_id, "status": "accepted"}

    @router.post("/sync/addons", status_code=202, dependencies=[Depends(require_role("sync"))])
    async def sync_addons(
        background_tasks: BackgroundTasks,
        settings: Settings = Depends(get_settings),
    ) -> dict:
        job_id = str(uuid.uuid4())
        nc = nc_getter()
        qdrant = qdrant_getter()
        background_tasks.add_task(_run_addons_sync, job_id, nc, qdrant, settings)
        return {"job_id": job_id, "status": "accepted"}

    @router.get("/sync/status/{job_id}", dependencies=[Depends(require_role("read", "sync"))])
    async def sync_status(job_id: str) -> dict:
        nc = nc_getter()
        try:
            kv = await nc.jetstream().key_value(_KV_SYNC_STATUS)
            entry = await kv.get(job_id)
            return json.loads(entry.value)
        except KeyNotFoundError:
            return {"status": "not_found"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
