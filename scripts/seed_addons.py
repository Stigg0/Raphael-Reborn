#!/usr/bin/env python
"""Seed external add-on/wiki documentation into Qdrant."""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _parse_urls(raw: str) -> list[str]:
    return [url.strip() for url in raw.split(",") if url.strip()]


def main() -> None:
    from config import get_settings
    settings = get_settings()
    urls = _parse_urls(settings.addon_source_urls)
    if not urls:
        logger.error("No ADDON_SOURCE_URLS configured.")
        sys.exit(1)

    from rag.client import get_client
    from rag.addons.indexer import build_addons_index
    from rag.addons.scraper import scrape_addon_sources

    qdrant = get_client(settings.qdrant_url, settings.qdrant_api_key)
    records = scrape_addon_sources(
        urls,
        force_refresh=True,
        mod_wiki_max_pages=settings.addon_mod_wiki_max_pages,
    )
    total = build_addons_index(
        qdrant, settings.addons_collection, records, settings.embed_model, reset=True,
    )
    logger.info("Done — %d add-on chunks indexed.", total)


if __name__ == "__main__":
    main()
