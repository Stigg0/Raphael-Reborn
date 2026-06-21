"""Small web-search helper for OpenAI-compatible tool calls."""
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import httpx

_DUCKDUCKGO_HTML = "https://html.duckduckgo.com/html/"
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
_MAX_QUERY_CHARS = 200
_MAX_FIELD_CHARS = 500


def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
    for name, value in attrs:
        if name == "class" and value:
            return set(value.split())
    return set()


def _href(attrs: list[tuple[str, str | None]]) -> str:
    for name, value in attrs:
        if name == "href" and value:
            return value
    return ""


def _decode_ddg_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return url


class _DuckDuckGoParser(HTMLParser):
    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = _classes(attrs)
        if tag == "a" and "result__a" in classes and len(self.results) < self.limit:
            if self._current is not None and self._current.get("title") and self._current.get("url"):
                self.results.append(self._current)
            self._current = {"title": "", "url": _decode_ddg_url(_href(attrs)), "snippet": ""}
            self._capture = "title"
            self._buffer = []
        elif self._current is not None and "result__snippet" in classes:
            self._capture = "snippet"
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture == "title" and tag == "a" and self._current is not None:
            self._current["title"] = " ".join("".join(self._buffer).split())[:_MAX_FIELD_CHARS]
            self._capture = None
            self._buffer = []
        elif self._capture == "snippet" and tag in {"a", "div", "span"} and self._current is not None:
            self._current["snippet"] = " ".join("".join(self._buffer).split())[:_MAX_FIELD_CHARS]
            self._capture = None
            self._buffer = []
            if self._current.get("title") and self._current.get("url"):
                self.results.append(self._current)
                self._current = None

    def close(self) -> None:
        super().close()
        if self._current is not None and self._current.get("title") and self._current.get("url"):
            self.results.append(self._current)
            self._current = None


def web_search(query: str, max_results: int = 5) -> str:
    """Return compact DuckDuckGo search results as plain text."""
    query = " ".join(query.split())[:_MAX_QUERY_CHARS]
    max_results = max(1, min(max_results, 8))
    if not query:
        return "Web search skipped: empty query."

    try:
        with httpx.Client(
            timeout=12,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        ) as client:
            resp = client.post(_DUCKDUCKGO_HTML, data={"q": query})
            resp.raise_for_status()
    except Exception as exc:
        return f"Web search failed for {query!r}: {type(exc).__name__}."

    parser = _DuckDuckGoParser(max_results)
    parser.feed(resp.text)
    parser.close()
    if not parser.results:
        return f"Web search found no results for {query!r}."

    lines = [f"Web search results for: {query}"]
    for idx, result in enumerate(parser.results[:max_results], start=1):
        lines.append(
            f"{idx}. {result['title']}\n"
            f"   URL: {result['url']}\n"
            f"   Snippet: {result.get('snippet') or 'No snippet available.'}"
        )
    return "\n".join(lines)
