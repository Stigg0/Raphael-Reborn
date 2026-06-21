"""Wikitext cleaning and chunking.

Ported from the original rag/indexer.py — no ChromaDB references here.
"""
import re

MAX_CHUNK_TOKENS = 400
CHARS_PER_TOKEN = 4
MAX_CHUNK_CHARS = MAX_CHUNK_TOKENS * CHARS_PER_TOKEN
MIN_CHUNK_CHARS = 20

_DATA_TEMPLATES = re.compile(
    r"(?:infobox|creature|skill|magic|race|item|effect)\b", re.IGNORECASE
)
_LAYOUT_TEMPLATE_PREFIXES = re.compile(
    r"(?:navbox|toc|wip|displaytitle|historytable|historyline|"
    r"skillsnavbox|magicsnavbox|mobsnavbox)\b",
    re.IGNORECASE,
)
_WIKI_LINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")
_IMAGE_KEY_RE = re.compile(r"^images?$", re.IGNORECASE)
_CATEGORY_LINK_RE = re.compile(r"\[\[Category:([^\]]+)\]\]", re.IGNORECASE)


def extract_categories(wikitext: str) -> list[str]:
    """Return a list of category names found in [[Category:X]] links."""
    return [m.group(1).strip() for m in _CATEGORY_LINK_RE.finditer(wikitext)]


def _expand_data_template(inner: str) -> str:
    resolved = _WIKI_LINK_RE.sub(r"\1", inner)
    pairs = re.findall(r"\|\s*([^=|{}\n]+?)\s*=\s*([^|{}\n]+)", resolved)
    lines = []
    for k, v in pairs:
        key, val = k.strip(), v.strip()
        if not key or not val or _IMAGE_KEY_RE.match(key):
            continue
        lines.append(f"{key}: {val}")
    return "\n".join(lines)


def clean_wikitext(raw: str) -> str:
    text = raw
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^>]*/?>", "", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{\{DescriptionEcho\|([^}]+)\}\}", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\{\{ItemLink\|([^|}]+)(?:\|[^}]*)?\}\}", r"\1", text, flags=re.IGNORECASE)

    def _maybe_expand(m: re.Match) -> str:
        name = m.group(1)
        if _DATA_TEMPLATES.match(name):
            return _expand_data_template(m.group(0))
        if _LAYOUT_TEMPLATE_PREFIXES.match(name):
            return ""
        return m.group(0)

    text = re.sub(
        r"\{\{([A-Za-z][^|{}\n]*?)\s*[\n|]((?:[^{}]|\{\{[^{}]*\}\})*)\}\}",
        _maybe_expand, text, flags=re.DOTALL,
    )
    text = re.sub(r"^\s*[|!]{1,2}", "", text, flags=re.MULTILINE)
    text = re.sub(r"\{\|.*?\|\}", "", text, flags=re.DOTALL)
    for _ in range(3):
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\]", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"'{2,3}", "", text)
    text = re.sub(r"={2,6}(.+?)={2,6}", r"\1", text)
    text = re.sub(r"^[\s*#:;]+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_sections(wikitext: str) -> list[tuple[str, str]]:
    pattern = re.compile(r"^={2,4}\s*(.+?)\s*={2,4}", re.MULTILINE)
    sections: list[tuple[str, str]] = []
    pos = 0
    current = "Overview"
    for m in pattern.finditer(wikitext):
        chunk = wikitext[pos:m.start()].strip()
        if chunk:
            sections.append((current, chunk))
        current = m.group(1).strip()
        pos = m.end()
    remainder = wikitext[pos:].strip()
    if remainder:
        sections.append((current, remainder))
    return sections


def _split_long_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current = (current + "\n\n" + para).strip() if current else para
        else:
            if current:
                chunks.append(current)
            if len(para) > max_chars:
                for i in range(0, len(para), max_chars):
                    chunks.append(para[i:i + max_chars])
            else:
                current = para
    if current:
        chunks.append(current)
    return chunks


def _extract_infobox_stats(wikitext: str) -> str:
    m = re.search(
        r"\{\{([A-Za-z][^|{}\n]*?)\s*[\n|]((?:[^{}]|\{\{[^{}]*\}\})*)\}\}",
        wikitext, re.DOTALL,
    )
    if not m or not _DATA_TEMPLATES.match(m.group(1)):
        return ""
    return _expand_data_template(m.group(0))


def build_chunks(page: dict) -> list[dict]:
    """Convert a raw wiki page into chunk dicts ready for embedding.

    Each dict has: text, page_title, section, url, chunk_type,
    page_category, page_path_segments, page_categories.
    """
    missing = [k for k in ("title", "url", "wikitext") if not page.get(k)]
    if missing:
        raise ValueError(f"Page missing keys: {missing}")
    title: str = page["title"]
    url: str = page["url"]
    wikitext: str = page["wikitext"]

    if wikitext.strip().upper().startswith("#REDIRECT"):
        return []

    parts = title.split("/")
    page_category = parts[0] if len(parts) > 1 else ""
    page_path_segments = ["/".join(parts[:i + 1]) for i in range(len(parts) - 1)]
    page_categories = extract_categories(wikitext)

    base_meta = {
        "page_title": title,
        "url": url,
        "page_category": page_category,
        "page_path_segments": page_path_segments,
        "page_categories": page_categories,
    }

    chunks: list[dict] = []

    stats = _extract_infobox_stats(wikitext)
    clean_full = clean_wikitext(wikitext)
    first_para = clean_full.split("\n\n")[0] if clean_full else ""
    summary_text = (f"{title}\n{stats}" if stats else f"{title}\n{first_para}").strip()
    if len(summary_text) >= MIN_CHUNK_CHARS:
        chunks.append({**base_meta, "text": summary_text[:MAX_CHUNK_CHARS], "section": "_summary", "chunk_type": "summary"})

    for section_name, raw_section in _split_sections(wikitext):
        clean = clean_wikitext(raw_section)
        if len(clean) < MIN_CHUNK_CHARS:
            continue
        prefix = f"{title} — {section_name}:\n"
        for part in _split_long_text(clean, MAX_CHUNK_CHARS - len(prefix)):
            if len(part) >= MIN_CHUNK_CHARS:
                chunks.append({**base_meta, "text": prefix + part, "section": section_name, "chunk_type": "section"})

    return chunks
