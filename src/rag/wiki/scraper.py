"""MediaWiki scraper for tensura.wiki.gg.

Ported from rag/scraper.py — logic unchanged, paths now driven by config.
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import mwclient
import requests

logger = logging.getLogger(__name__)

RATE_DELAY = 1.5
RETRY_DELAYS = [30, 60, 120]
MAX_PRUNE_RATIO = 0.25

_DATA_DIR = Path("/app/data")
CACHE_DIR = _DATA_DIR / "pages"
TIMESTAMP_FILE = _DATA_DIR / "last_updated.txt"


def connect(host: str) -> mwclient.Site:
    return mwclient.Site(host, path="/")


def _cache_path(title: str) -> Path:
    safe = title.replace("/", "_").replace(" ", "_")
    resolved = (CACHE_DIR / f"{safe}.json").resolve()
    if resolved.parent != CACHE_DIR.resolve():
        raise ValueError(f"Path traversal detected: {title!r}")
    return resolved


def _fetch_wikitext(site: mwclient.Site, title: str) -> str:
    for attempt, wait in enumerate(RETRY_DELAYS, 1):
        try:
            return site.pages[title].text()
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                logger.warning("HTTP 429 — waiting %ds (retry %d/%d)", wait, attempt, len(RETRY_DELAYS))
                time.sleep(wait)
            else:
                raise
        except mwclient.errors.APIError as e:
            if e.code == "ratelimited":
                logger.warning("API ratelimited — waiting %ds (retry %d/%d)", wait, attempt, len(RETRY_DELAYS))
                time.sleep(wait)
            else:
                raise
    return site.pages[title].text()


def fetch_all_pages(host: str, force_refresh: bool = False) -> list[dict]:
    """Full scrape of all wiki pages. Uses disk cache unless force_refresh."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    site = connect(host)
    all_titles = [page.name for page in site.allpages()]
    logger.info("Found %d pages on wiki", len(all_titles))

    results: list[dict] = []
    for i, title in enumerate(all_titles, 1):
        path = _cache_path(title)
        if path.exists() and not force_refresh:
            results.append(json.loads(path.read_text(encoding="utf-8")))
            continue
        try:
            wikitext = _fetch_wikitext(site, title)
            data = {
                "title": title,
                "wikitext": wikitext,
                "url": f"https://{host}/wiki/{title.replace(' ', '_')}",
            }
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            results.append(data)
            logger.info("[%d/%d] Fetched: %s", i, len(all_titles), title)
            time.sleep(RATE_DELAY)
        except Exception as exc:
            logger.error("[%d/%d] ERROR fetching %r: %s", i, len(all_titles), title, exc)

    logger.info("Scrape complete — %d pages ready.", len(results))
    return results


def fetch_updated_pages(host: str, since: str) -> list[dict]:
    """Fetch only pages changed since the given ISO timestamp."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    site = connect(host)
    logger.info("Querying recentchanges since %s", since)

    changed_titles: set[str] = set()
    for rc_type in ("edit", "new", "log"):
        for change in site.recentchanges(end=since, dir="older", prop=["title"], type=[rc_type], namespace=0):
            changed_titles.add(change["title"])

    if not changed_titles:
        logger.info("No pages changed since %s", since)
        return []

    logger.info("Found %d changed pages", len(changed_titles))
    results: list[dict] = []
    for i, title in enumerate(sorted(changed_titles), 1):
        try:
            wikitext = _fetch_wikitext(site, title)
            data = {
                "title": title,
                "wikitext": wikitext,
                "url": f"https://{host}/wiki/{title.replace(' ', '_')}",
            }
            path = _cache_path(title)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            results.append(data)
            logger.info("[%d/%d] Fetched: %s", i, len(changed_titles), title)
            time.sleep(RATE_DELAY)
        except Exception as exc:
            logger.error("[%d/%d] ERROR fetching %r: %s", i, len(changed_titles), title, exc)

    return results


def load_cached_pages() -> list[dict]:
    """Load all pages from disk cache (no network call)."""
    if not CACHE_DIR.exists():
        return []
    pages = []
    for path in CACHE_DIR.glob("*.json"):
        try:
            pages.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning("Failed to load cache file %s: %s", path, exc)
    return pages


def get_last_updated() -> str | None:
    if TIMESTAMP_FILE.exists():
        return TIMESTAMP_FILE.read_text(encoding="utf-8").strip()
    return None


def set_last_updated(ts: str | None = None) -> None:
    if ts is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    TIMESTAMP_FILE.parent.mkdir(parents=True, exist_ok=True)
    TIMESTAMP_FILE.write_text(ts, encoding="utf-8")
