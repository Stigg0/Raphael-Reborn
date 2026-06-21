#!/usr/bin/env python
"""One-time subtitle seed script.

Drop .srt / .ass / .vtt files into the subtitles/ directory, then run:
  python scripts/seed_subtitles.py

Files should follow naming conventions like:
  That.Time.I.Got.Reincarnated.as.a.Slime.S01E01.srt
  tensura_S2E03.ass

The season/episode is extracted from the filename automatically.
Speaker attribution requires ASS/SSA files with style names, or inline
tags like "RAPHAEL: " or "[Raphael] " at the start of SRT lines.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    subtitle_dir = Path(__file__).parent.parent / "subtitles"
    if not subtitle_dir.exists() or not any(subtitle_dir.rglob("*")):
        logger.error(
            "No subtitle files found in %s\n"
            "Drop .srt, .ass, or .vtt files there and re-run.", subtitle_dir,
        )
        sys.exit(1)

    from config import get_settings
    settings = get_settings()

    from rag.client import get_client
    qdrant = get_client(settings.qdrant_url, settings.qdrant_api_key)

    from rag.subtitles.indexer import build_subtitle_index
    # raphael_only=True requires ASS/SSA files with character names in the Style field.
    # Plain SRT files (e.g. from SubDL) have no speaker info, so all lines get is_raphael=False.
    # Index everything for lore context; persona filtering uses is_raphael at query time.
    total = build_subtitle_index(
        qdrant, settings.subtitles_collection, subtitle_dir, settings.embed_model,
        reset=True, raphael_only=False,
    )
    logger.info("Done — %d subtitle chunks indexed.", total)


if __name__ == "__main__":
    main()
