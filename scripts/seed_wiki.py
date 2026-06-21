#!/usr/bin/env python
"""One-time wiki seed script. Run once at first deploy or to do a full re-index.

Usage:
  python scripts/seed_wiki.py                   # full scrape + index
  python scripts/seed_wiki.py --cached          # re-index from disk cache only
  python scripts/seed_wiki.py --incremental     # only changed pages since last sync
  python scripts/seed_wiki.py --refresh         # force re-fetch all pages
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed or sync the wiki index in Qdrant")
    parser.add_argument("--cached", action="store_true", help="Re-index from disk cache (no network)")
    parser.add_argument("--incremental", action="store_true", help="Only index pages changed since last sync")
    parser.add_argument("--refresh", action="store_true", help="Force re-fetch all pages from wiki")
    args = parser.parse_args()

    from config import get_settings
    settings = get_settings()

    from rag.client import get_client
    qdrant = get_client(settings.qdrant_url, settings.qdrant_api_key)

    from rag.wiki.indexer import build_index, reindex_page
    from rag.wiki.scraper import (
        fetch_all_pages,
        fetch_updated_pages,
        get_last_updated,
        load_cached_pages,
        set_last_updated,
    )

    if args.cached:
        logger.info("Loading pages from disk cache...")
        pages = load_cached_pages()
        logger.info("Loaded %d cached pages", len(pages))
    elif args.incremental:
        since = get_last_updated()
        if not since:
            logger.info("No last_updated timestamp — doing full scrape")
            pages = fetch_all_pages(settings.wiki_host)
        else:
            logger.info("Incremental sync since %s", since)
            pages = fetch_updated_pages(settings.wiki_host, since)
            logger.info("Found %d changed pages", len(pages))
            for page in pages:
                reindex_page(qdrant, settings.wiki_collection, page, settings.embed_model)
            set_last_updated()
            logger.info("Incremental sync complete.")
            return
    else:
        pages = fetch_all_pages(settings.wiki_host, force_refresh=args.refresh)

    total = build_index(qdrant, settings.wiki_collection, pages, settings.embed_model)
    set_last_updated()
    logger.info("Done — %d chunks indexed.", total)


if __name__ == "__main__":
    main()
