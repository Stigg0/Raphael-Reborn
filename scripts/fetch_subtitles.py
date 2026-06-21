#!/usr/bin/env python
"""Download English subtitle files for all Tensura episodes via SubDL.com.

SubDL.com offers a free API tier — register at https://subdl.com to get a key.
Set SUBDL_API_KEY in your .env file (or as an environment variable) then run:

  docker compose run --rm api python scripts/seed_subtitles.py  # after this script

Usage:
  SUBDL_API_KEY=<your_key> python scripts/fetch_subtitles.py
  # or set it in .env and run inside the api container:
  docker compose run --rm api python scripts/fetch_subtitles.py

Downloaded files land in subtitles/ — already-present episodes are skipped.
"""
import io
import logging
import os
import sys
import time
import zipfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_SUBTITLE_DIR = Path(__file__).parent.parent / "subtitles"
_SUBDL_SEARCH = "https://api.subdl.com/api/v1/subtitles"
_SUBDL_DL_BASE = "https://dl.subdl.com"

_SERIES_NAMES = [
    "That Time I Got Reincarnated as a Slime",
    "Tensei shitara Slime Datta Ken",
]
_SEASONS = [1, 2, 3]
_MAX_EP_PER_SEASON = 50
_GIVE_UP_AFTER = 3
_SEARCH_DELAY = 0.5


def _already_downloaded(season: int, episode: int) -> bool:
    for ext in ("ass", "ssa", "srt", "vtt"):
        if (_SUBTITLE_DIR / f"S{season:02d}E{episode:03d}.en.{ext}").exists():
            return True
    return False


def _search(client: httpx.Client, api_key: str, series: str, season: int, ep: int) -> list[dict]:
    try:
        resp = client.get(
            _SUBDL_SEARCH,
            params={
                "api_key": api_key,
                "film_name": series,
                "season_number": str(season),
                "episode_number": str(ep),
                "languages": "EN",
                "type": "tv",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json().get("subtitles", [])
    except Exception as exc:
        logger.debug("Search error: %s", exc)
        return []


def _best_sub(results: list[dict]) -> dict | None:
    """Pick fullSeason=False, then highest release_year (most recent), prefer SRT."""
    eps = [r for r in results if not r.get("season_episode_id") or True]
    if not eps:
        return None
    srt = [r for r in eps if r.get("lang", "").upper() == "EN"]
    if not srt:
        srt = eps
    return srt[0] if srt else None


def _download(client: httpx.Client, sub: dict, season: int, ep: int) -> bool:
    url = sub.get("url", "")
    if not url:
        return False
    try:
        if not url.startswith("http"):
            url = _SUBDL_DL_BASE + url
        resp = client.get(url, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        for name in zf.namelist():
            lower = name.lower()
            if lower.endswith((".srt", ".ass", ".ssa", ".vtt")):
                ext = lower.rsplit(".", 1)[-1]
                dest = _SUBTITLE_DIR / f"S{season:02d}E{ep:03d}.en.{ext}"
                dest.write_bytes(zf.read(name))
                logger.info("S%02dE%03d → %s  (%s)", season, ep, dest.name, sub.get("release_name", ""))
                return True
    except Exception as exc:
        logger.debug("Download error: %s", exc)
    return False


def main() -> None:
    # Load .env if present
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("SUBDL_API_KEY=") and "=" in line:
                os.environ.setdefault("SUBDL_API_KEY", line.split("=", 1)[1].strip().strip('"'))

    api_key = os.environ.get("SUBDL_API_KEY", "").strip()
    if not api_key:
        logger.error(
            "SUBDL_API_KEY is not set.\n"
            "  1. Register (free) at https://subdl.com\n"
            "  2. Copy your API key from your account settings\n"
            "  3. Add SUBDL_API_KEY=<key> to your .env file\n"
            "  4. Re-run this script inside the api container:\n"
            "       docker compose run --rm api python scripts/fetch_subtitles.py"
        )
        sys.exit(1)

    _SUBTITLE_DIR.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    skipped = 0

    with httpx.Client(headers={"User-Agent": "RaphaelBot/0.1"}) as client:
        for season in _SEASONS:
            consecutive_misses = 0
            logger.info("=== Season %d ===", season)

            for ep in range(1, _MAX_EP_PER_SEASON + 1):
                if _already_downloaded(season, ep):
                    skipped += 1
                    consecutive_misses = 0
                    continue

                time.sleep(_SEARCH_DELAY)

                results: list[dict] = []
                for series in _SERIES_NAMES:
                    results = _search(client, api_key, series, season, ep)
                    if results:
                        break

                sub = _best_sub(results)
                if not sub:
                    logger.warning("S%02dE%03d — no subtitle found.", season, ep)
                    consecutive_misses += 1
                    if consecutive_misses >= _GIVE_UP_AFTER:
                        logger.info("S%02d — %d consecutive misses, moving on.", season, _GIVE_UP_AFTER)
                        break
                    continue

                time.sleep(_SEARCH_DELAY)
                if _download(client, sub, season, ep):
                    downloaded += 1
                    consecutive_misses = 0
                else:
                    logger.warning("S%02dE%03d — download failed.", season, ep)
                    consecutive_misses += 1
                    if consecutive_misses >= _GIVE_UP_AFTER:
                        logger.info("S%02d — %d consecutive misses, moving on.", season, _GIVE_UP_AFTER)
                        break

    logger.info("\nDone — %d downloaded, %d already present.", downloaded, skipped)
    if downloaded > 0:
        logger.info("Next step:  docker compose run --rm api python scripts/seed_subtitles.py")


if __name__ == "__main__":
    main()
