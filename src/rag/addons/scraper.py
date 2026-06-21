"""Scrape external add-on project pages into cacheable text records."""
import hashlib
import ast
import io
import json
import logging
import os
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

from rag.safe_http import safe_get

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.getenv("RAG_DATA_DIR", "/app/data"))
CACHE_DIR = _DATA_DIR / "addons"

_DEFAULT_TIMEOUT = 30
_MAX_DOC_CHARS = 9000
_MAX_DOC_LINKS = 120
_USER_AGENT = "Raphael-Reborn/0.1 (+local RAG indexer)"
_MODRINTH_PROJECT_ID_RE = re.compile(r"/data/([^/]+)/versions/")
_SCRIPT_RE = re.compile(r"<script\b[^>]*\bsrc=[\"']([^\"']+\.js)[\"'][^>]*>", re.IGNORECASE)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_RE = re.compile(
    r"<meta\b[^>]*(?:name|property)=[\"']([^\"']+)[\"'][^>]*content=[\"']([^\"']*)[\"'][^>]*>",
    re.IGNORECASE | re.DOTALL,
)
_DOMAIN_KEYWORDS = (
    "tensura", "dungeon", "rimuru", "colosseum", "floor", "boss", "ep", "hp",
    "armor", "attack", "ability", "abilities", "reward", "rewards", "coin",
    "coins", "map", "entry", "requirement", "kurobee", "dryad", "guild",
    "kaijin", "spider", "centipede", "ogre", "serpent", "mezul", "gozul",
    "charybdis", "elemental", "dominators", "voucher", "loot", "trap",
)
_NOISE_RE = re.compile(
    r"react|className|rounded-md|text-muted|foreground|background|border-|"
    r"currentColor|lucide|jsx|children dangerously|alignment-baseline|"
    r"pointer-events|transition-colors|hover:|auxClick|gotPointerCapture|"
    r"text-xl|drop-shadow|font-semibold|card-game|mb-",
    re.IGNORECASE,
)


class _ReadableHTMLParser(HTMLParser):
    """Small HTML-to-text parser tuned for project/docs pages."""

    _BLOCK_TAGS = {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
        "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
        "section", "table", "td", "th", "tr", "ul",
    }
    _SKIP_TAGS = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = " ".join(data.split())
        if text:
            self._parts.append(text)
            self._parts.append(" ")

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = []
        for line in raw.splitlines():
            cleaned = " ".join(line.split())
            if cleaned:
                lines.append(cleaned)
        return "\n".join(lines)


class _LinkHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.links.append(value)


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    host = urlparse(url).netloc.replace(":", "_") or "source"
    return CACHE_DIR / f"{host}_{digest}.json"


def _source_type(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "curseforge.com" in host:
        return "curseforge"
    if "modrinth.com" in host:
        return "modrinth"
    return host or "external"


def _modrinth_slug(url: str) -> str | None:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if parsed.netloc == "api.modrinth.com" and len(parts) >= 3 and parts[:2] == ["v2", "project"]:
        return parts[2]
    if "modrinth.com" not in parsed.netloc:
        return None
    if len(parts) >= 2 and parts[0] in {"mod", "modpack"}:
        return parts[1]
    return None


def _scrape_modrinth_project(
    url: str,
    force_refresh: bool = False,
    mod_wiki_max_pages: int = 8,
) -> dict | None:
    slug = _modrinth_slug(url)
    if not slug:
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(url)
    if path.exists() and not force_refresh:
        return json.loads(path.read_text(encoding="utf-8"))

    headers = {"User-Agent": _USER_AGENT}
    project_resp = safe_get(
        f"https://api.modrinth.com/v2/project/{slug}",
        headers=headers,
        timeout=_DEFAULT_TIMEOUT,
    )
    project_resp.raise_for_status()
    project = project_resp.json()

    versions_resp = safe_get(
        f"https://api.modrinth.com/v2/project/{slug}/version",
        headers=headers,
        timeout=_DEFAULT_TIMEOUT,
    )
    versions_resp.raise_for_status()
    versions = versions_resp.json()

    overview_lines = [
        project.get("title", ""),
        f"Project type: {project.get('project_type', '')}",
        f"Description: {project.get('description', '')}",
        f"Supported Minecraft game versions: {', '.join(project.get('game_versions') or [])}",
        f"Client side: {project.get('client_side', '')}",
        f"Server side: {project.get('server_side', '')}",
        f"Wiki URL: {project.get('wiki_url') or ''}",
        f"Source URL: {project.get('source_url') or ''}",
        f"Issues URL: {project.get('issues_url') or ''}",
        "",
        "Project description:",
        project.get("body", ""),
    ]
    sections = [{"section": "Project Overview", "text": "\n".join(overview_lines).strip()}]
    all_lines = list(overview_lines) + ["", "Versions and changelogs:"]

    for version in versions:
        game_versions = ", ".join(version.get("game_versions") or [])
        loaders = ", ".join(version.get("loaders") or [])
        version_lines = [
            f"Version: {version.get('name', '')}",
            f"Modpack/project release version number: {version.get('version_number', '')}",
            f"Date published: {version.get('date_published', '')}",
            f"Supported Minecraft game versions: {game_versions}",
            f"Loaders: {loaders}",
            f"Version type: {version.get('version_type', '')}",
        ]
        changelog = version.get("changelog") or ""
        if changelog:
            version_lines.append("Changelog:")
            version_lines.append(changelog)
        section_text = "\n".join(version_lines).strip()
        sections.append({
            "section": f"Changelog {version.get('version_number', '')}".strip(),
            "text": section_text,
        })
        all_lines.extend(["", section_text])

    bundled_sections = _scrape_modrinth_modpack_projects(
        project, versions, headers, mod_wiki_max_pages=mod_wiki_max_pages,
    )
    if bundled_sections:
        sections.extend(bundled_sections)
        all_lines.extend(["", "Bundled mod metadata:"])
        all_lines.extend(section["text"] for section in bundled_sections)

    text = "\n".join(line for line in all_lines if line is not None).strip()
    record = {
        "url": url,
        "source_type": "modrinth",
        "project_title": project.get("title", slug),
        "text": text,
        "sections": sections,
    }
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    logger.info("Scraped Modrinth project %s (%d chars, %d versions)", slug, len(text), len(versions))
    return record


def _latest_modpack_file(versions: list[dict]) -> dict | None:
    for version in versions:
        files = version.get("files") or []
        primary = next((f for f in files if f.get("primary")), None)
        candidates = [primary] if primary else []
        candidates.extend(f for f in files if f is not primary)
        for file_info in candidates:
            filename = (file_info or {}).get("filename", "")
            if filename.endswith(".mrpack") and file_info.get("url"):
                return file_info
    return None


def _project_ids_from_mrpack(file_info: dict, headers: dict[str, str]) -> list[str]:
    resp = safe_get(file_info["url"], headers=headers, timeout=_DEFAULT_TIMEOUT)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        with archive.open("modrinth.index.json") as manifest_file:
            manifest = json.loads(manifest_file.read().decode("utf-8"))

    project_ids: list[str] = []
    seen: set[str] = set()
    for file_entry in manifest.get("files") or []:
        for download_url in file_entry.get("downloads") or []:
            match = _MODRINTH_PROJECT_ID_RE.search(download_url)
            if not match:
                continue
            project_id = match.group(1)
            if project_id not in seen:
                seen.add(project_id)
                project_ids.append(project_id)
    return project_ids


def _scrape_modrinth_modpack_projects(
    project: dict,
    versions: list[dict],
    headers: dict[str, str],
    mod_wiki_max_pages: int = 8,
) -> list[dict]:
    if project.get("project_type") != "modpack":
        return []

    pack_file = _latest_modpack_file(versions)
    if not pack_file:
        return []

    try:
        project_ids = _project_ids_from_mrpack(pack_file, headers)
    except Exception as exc:
        logger.warning("Failed to inspect Modrinth modpack manifest: %s", exc)
        return []

    sections = []
    for project_id in project_ids:
        try:
            resp = safe_get(
                f"https://api.modrinth.com/v2/project/{project_id}",
                headers=headers,
                timeout=_DEFAULT_TIMEOUT,
            )
            resp.raise_for_status()
            mod = resp.json()
        except Exception as exc:
            logger.debug("Failed to fetch bundled Modrinth project %s: %s", project_id, exc)
            continue

        title = mod.get("title") or mod.get("slug") or project_id
        body = (mod.get("body") or "").strip()
        if len(body) > 4000:
            body = body[:4000].rsplit(" ", 1)[0] + "..."
        lines = [
            f"Bundled project: {title}",
            f"Slug: {mod.get('slug', '')}",
            f"Project type: {mod.get('project_type', '')}",
            f"Description: {mod.get('description', '')}",
            f"Supported Minecraft game versions: {', '.join(mod.get('game_versions') or [])}",
            f"Loaders: {', '.join(mod.get('loaders') or [])}",
            f"Client side: {mod.get('client_side', '')}",
            f"Server side: {mod.get('server_side', '')}",
            f"Categories: {', '.join(mod.get('categories') or [])}",
            f"URL: https://modrinth.com/{mod.get('project_type', 'mod')}/{mod.get('slug', project_id)}",
        ]
        if body:
            lines.extend(["", "Project description:", body])
        sections.append({
            "section": f"Bundled Mod - {title}",
            "text": "\n".join(lines).strip(),
        })
        wiki_url = mod.get("wiki_url")
        if wiki_url and mod_wiki_max_pages > 0:
            sections.extend(_scrape_mod_wiki_sections(title, wiki_url, headers, mod_wiki_max_pages))

    logger.info("Scraped %d bundled Modrinth projects from modpack manifest", len(sections))
    return sections


def _safe_doc_url(url: str) -> str | None:
    cleaned, _fragment = urldefrag(url)
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.path.lower().endswith((
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
        ".zip", ".jar", ".mrpack", ".json", ".xml", ".pdf",
    )):
        return None
    return cleaned.rstrip("/")


def _doc_scope(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    path = parsed.path or "/"
    parts = [p for p in path.split("/") if p]
    if parsed.netloc == "github.com" and len(parts) >= 3 and parts[2] == "wiki":
        return parsed.netloc, "/" + "/".join(parts[:3])
    if "wiki" in parts:
        return parsed.netloc, "/" + "/".join(parts[:parts.index("wiki") + 1])
    if path.endswith("/"):
        return parsed.netloc, path.rstrip("/") or "/"
    parent = path.rsplit("/", 1)[0]
    return parsed.netloc, parent or "/"


def _in_doc_scope(url: str, host: str, prefix: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc != host:
        return False
    if prefix == "/":
        return True
    return parsed.path == prefix or parsed.path.startswith(prefix.rstrip("/") + "/")


def _html_to_readable_text(url: str, html: str) -> tuple[str, str]:
    head_title, meta_lines = _head_metadata(html)
    parser = _ReadableHTMLParser()
    parser.feed(html)
    parts = []
    if head_title:
        parts.append(head_title)
    parts.extend(meta_lines)
    body = parser.text()
    if body:
        parts.append(body)
    text = _curate_text_for_url(url, "\n".join(parts))
    return head_title, text[:_MAX_DOC_CHARS]


def _extract_doc_links(base_url: str, html: str, host: str, prefix: str) -> list[str]:
    parser = _LinkHTMLParser()
    parser.feed(html)
    links: list[str] = []
    seen: set[str] = set()
    for href in parser.links:
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = _safe_doc_url(urljoin(base_url, href))
        if not url or url in seen or not _in_doc_scope(url, host, prefix):
            continue
        seen.add(url)
        links.append(url)
        if len(links) >= _MAX_DOC_LINKS:
            break
    return links


def _scrape_mod_wiki_sections(
    mod_title: str,
    wiki_url: str,
    headers: dict[str, str],
    max_pages: int,
) -> list[dict]:
    start_url = _safe_doc_url(wiki_url)
    if not start_url:
        return []

    host, prefix = _doc_scope(start_url)
    queue = [start_url]
    seen: set[str] = set()
    sections: list[dict] = []

    while queue and len(sections) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            resp = safe_get(url, headers=headers, timeout=_DEFAULT_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            logger.debug("Failed to fetch wiki page for %s (%s): %s", mod_title, url, exc)
            continue
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and "charset" not in content_type:
            continue

        title, text = _html_to_readable_text(url, resp.text)
        if len(text) >= 120:
            sections.append({
                "section": f"Bundled Mod Wiki - {mod_title} - {title or urlparse(url).path.rstrip('/').rsplit('/', 1)[-1]}",
                "text": f"Documentation source: {url}\nBundled project: {mod_title}\n\n{text}",
            })

        for link in _extract_doc_links(url, resp.text, host, prefix):
            if link not in seen and link not in queue:
                queue.append(link)

    if sections:
        logger.info("Scraped %d wiki/doc pages for bundled mod %s", len(sections), mod_title)
    return sections


def _head_metadata(html: str) -> tuple[str, list[str]]:
    title = ""
    m = _TITLE_RE.search(html)
    if m:
        title = " ".join(m.group(1).split())
    meta_lines = []
    for key, value in _META_RE.findall(html):
        key = key.strip().lower()
        value = " ".join(value.split())
        if value and key in {"description", "og:description", "twitter:description", "author", "og:title"}:
            meta_lines.append(f"{key}: {value}")
    return title, meta_lines


def _decode_js_string(quote: str, raw: str) -> str:
    if quote == "`":
        value = raw.replace("\\`", "`")
    else:
        try:
            value = ast.literal_eval(quote + raw + quote)
        except Exception:
            value = raw
    value = re.sub(r"\$\{[^}]+\}", " ", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = " ".join(value.split())
    return value


def _extract_js_text(js: str) -> str:
    """Extract readable string literals from bundled JS without regex backtracking."""
    values = []
    i = 0
    n = len(js)
    while i < n:
        quote = js[i]
        if quote not in {"'", '"', "`"}:
            i += 1
            continue
        i += 1
        buf: list[str] = []
        escaped = False
        while i < n:
            ch = js[i]
            i += 1
            if escaped:
                buf.append("\\" + ch)
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == quote:
                break
            if len(buf) > 500:
                break
            buf.append(ch)
        raw = "".join(buf)
        value = _decode_js_string(quote, raw)
        if len(value) < 4:
            continue
        if not re.search(r"[A-Za-z]", value):
            continue
        if re.search(r"^[a-z0-9_:/.-]+$", value) and " " not in value:
            continue
        if value.startswith(("http://", "https://", "/", "data:")):
            continue
        if _NOISE_RE.search(value):
            continue
        values.append(value)
        i += 1

    keep_indexes: set[int] = set()
    for idx, value in enumerate(values):
        lower = value.lower()
        if any(keyword in lower for keyword in _DOMAIN_KEYWORDS):
            start = max(0, idx - 1)
            end = min(len(values), idx + 2)
            keep_indexes.update(range(start, end))

    lines = []
    seen: set[str] = set()
    for idx in sorted(keep_indexes):
        value = values[idx]
        if value in seen:
            continue
        seen.add(value)
        lines.append(value)
    return "\n".join(lines)


def _linked_script_text(url: str, html: str) -> str:
    script_texts = []
    for src in _SCRIPT_RE.findall(html):
        script_url = urljoin(url, src)
        try:
            resp = safe_get(script_url, headers={"User-Agent": _USER_AGENT}, timeout=_DEFAULT_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            logger.debug("Failed to fetch linked script %s: %s", script_url, exc)
            continue
        extracted = _extract_js_text(resp.text)
        if extracted:
            script_texts.append(extracted)
    return "\n".join(script_texts)


def _extract_title(text: str, url: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if line.lower() in {"download", "install", "description", "details"}:
            continue
        if 3 <= len(line) <= 80:
            return line
    slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return slug.replace("-", " ").title() if slug else url


def _extract_sections(text: str) -> list[dict]:
    """Split readable page text into heading-ish sections."""
    headings = {
        "about the add-on", "how to enter", "server setup tips",
        "commands / configs / gamerules", "commands", "gamerules", "configs",
        "new update", "file details", "changelog", "gameplay & systems",
        "progression & balance", "important notes", "the tensura dungeon team",
    }
    sections: list[dict] = []
    current = "Overview"
    buf: list[str] = []
    for line in text.splitlines():
        normalized = line.strip().lower().strip("#: ")
        is_heading = normalized in headings or (
            len(line) <= 70 and normalized.startswith(("about ", "how ", "server ", "gameplay ", "progression "))
        )
        if is_heading and buf:
            sections.append({"section": current, "text": "\n".join(buf).strip()})
            current = line.strip(" #:")
            buf = []
        elif is_heading:
            current = line.strip(" #:")
        else:
            buf.append(line)
    if buf:
        sections.append({"section": current, "text": "\n".join(buf).strip()})
    return [s for s in sections if s["text"]]


def _curate_curseforge_text(text: str) -> str:
    """Drop common navigation/footer noise while preserving project details."""
    keep_after = re.search(r"(?im)^#?\s*Tensura Dungeon\s*$", text)
    if keep_after:
        text = text[keep_after.start():]
    stop = re.search(r"(?im)^Discover\s+Browse\s+Search$|^CurseForge - a world", text)
    if stop:
        text = text[:stop.start()]
    return text.strip()


def _curate_text_for_url(url: str, text: str) -> str:
    host = urlparse(url).netloc.lower()
    if "curseforge.com" in host:
        return _curate_curseforge_text(text)
    return text.strip()


def scrape_addon_url(url: str, force_refresh: bool = False, mod_wiki_max_pages: int = 8) -> dict:
    """Fetch one add-on project URL and cache a normalized record."""
    modrinth_record = _scrape_modrinth_project(
        url, force_refresh=force_refresh, mod_wiki_max_pages=mod_wiki_max_pages,
    )
    if modrinth_record is not None:
        return modrinth_record

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(url)
    if path.exists() and not force_refresh:
        return json.loads(path.read_text(encoding="utf-8"))

    resp = safe_get(url, headers={"User-Agent": _USER_AGENT}, timeout=_DEFAULT_TIMEOUT)
    resp.raise_for_status()
    head_title, meta_lines = _head_metadata(resp.text)
    parser = _ReadableHTMLParser()
    parser.feed(resp.text)
    parts = []
    if head_title:
        parts.append(head_title)
    parts.extend(meta_lines)
    body_text = parser.text()
    if body_text:
        parts.append(body_text)
    script_text = _linked_script_text(url, resp.text)
    if script_text:
        parts.append(script_text)
    text = _curate_text_for_url(url, "\n".join(parts))
    record = {
        "url": url,
        "source_type": _source_type(url),
        "project_title": head_title or _extract_title(text, url),
        "text": text,
        "sections": _extract_sections(text),
    }
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    logger.info("Scraped add-on source %s (%d chars)", url, len(text))
    return record


def scrape_addon_sources(
    urls: list[str],
    force_refresh: bool = False,
    mod_wiki_max_pages: int = 8,
) -> list[dict]:
    """Scrape all configured add-on URLs."""
    records = []
    for url in urls:
        try:
            records.append(scrape_addon_url(
                url,
                force_refresh=force_refresh,
                mod_wiki_max_pages=mod_wiki_max_pages,
            ))
        except Exception as exc:
            logger.error("Failed to scrape add-on source %s: %s", url, exc)
    return records
