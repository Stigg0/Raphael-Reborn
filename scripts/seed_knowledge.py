#!/usr/bin/env python
"""Rebuild all searchable knowledge collections.

This is the reproducible bootstrap entrypoint for a fresh deployment:
- main Tensura wiki -> wiki collection
- TRBeyond wiki/add-on/modpack sources -> addons collection
- local subtitle files, when present -> subtitles collection
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _parse_urls(raw: str) -> list[str]:
    return [url.strip() for url in raw.split(",") if url.strip()]


def _has_subtitle_files(path: Path) -> bool:
    return any(path.glob(pattern) for pattern in ("*.srt", "*.ass", "*.ssa", "*.vtt"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed wiki, external add-on docs, and subtitles into Qdrant.")
    parser.add_argument("--skip-wiki", action="store_true", help="Do not rebuild the main tensura.wiki.gg collection.")
    parser.add_argument("--skip-addons", action="store_true", help="Do not rebuild the external add-ons collection.")
    parser.add_argument("--subtitles", action="store_true", help="Also rebuild subtitles even if no files are detected.")
    parser.add_argument("--skip-subtitles", action="store_true", help="Do not rebuild subtitles.")
    args = parser.parse_args()

    from config import get_settings
    from rag.client import get_client

    settings = get_settings()
    qdrant = get_client(settings.qdrant_url, settings.qdrant_api_key)

    if not args.skip_wiki:
        from rag.wiki.indexer import build_index
        from rag.wiki.scraper import fetch_all_pages, set_last_updated

        logger.info("Seeding main wiki from %s into %s...", settings.wiki_host, settings.wiki_collection)
        pages = fetch_all_pages(settings.wiki_host, force_refresh=True)
        count = build_index(qdrant, settings.wiki_collection, pages, settings.embed_model)
        set_last_updated()
        logger.info("Main wiki seeded: %d chunks/pages indexed.", count)

    if not args.skip_addons:
        from rag.addons.indexer import build_addons_index
        from rag.addons.scraper import scrape_addon_sources

        urls = _parse_urls(settings.addon_source_urls)
        if not urls:
            logger.warning("No ADDON_SOURCE_URLS configured; skipping add-ons.")
        else:
            logger.info("Seeding external add-on/wiki sources into %s: %s", settings.addons_collection, urls)
            records = scrape_addon_sources(
                urls,
                force_refresh=True,
                mod_wiki_max_pages=settings.addon_mod_wiki_max_pages,
            )
            count = build_addons_index(
                qdrant, settings.addons_collection, records, settings.embed_model, reset=True,
            )
            logger.info("External add-ons/wiki seeded: %d chunks indexed.", count)

    if not args.skip_subtitles:
        subtitle_dir = Path("/app/subtitles")
        local_fallback = Path(__file__).parent.parent / "subtitles"
        if not subtitle_dir.exists() and local_fallback.exists():
            subtitle_dir = local_fallback
        if args.subtitles or _has_subtitle_files(subtitle_dir):
            from rag.subtitles.indexer import build_subtitle_index

            logger.info("Seeding subtitles from %s into %s...", subtitle_dir, settings.subtitles_collection)
            count = build_subtitle_index(
                qdrant, settings.subtitles_collection, subtitle_dir, settings.embed_model, reset=True,
            )
            logger.info("Subtitles seeded: %d chunks indexed.", count)
        else:
            logger.info("No subtitle files detected in %s; skipping subtitles.", subtitle_dir)


if __name__ == "__main__":
    main()
