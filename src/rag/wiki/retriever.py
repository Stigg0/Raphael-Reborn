"""Wiki retriever — orchestrates Qdrant queries, title caching, category routing.

Ported from the original 1022-line rag/retriever.py, decomposed into:
  categories.py — category/enumeration detection
  normalizer.py — query normalisation, abbreviation expansion, meta-detection
  scoring.py    — dynamic text-contains scoring, blocking, condensation
  retriever.py  — this file (thin orchestrator)
"""
import difflib
import logging
import re
import threading

from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    MatchAny,
    MatchText,
    MatchValue,
)

from rag.embedder import embed_one
from rag.wiki.categories import (
    STRATEGY_NOUN,
    detect_category,
    detect_enumeration,
    is_comparative,
)
from rag.wiki.normalizer import (
    expand_abbreviations,
    is_meta_question,
    normalize_query,
)
from rag.wiki.scoring import (
    append_truncation_notice,
    condense_chunks,
    dynamic_score,
    is_blocked,
)

logger = logging.getLogger(__name__)

QUERY_PREFIX = "In the Tensura Minecraft mod, "
_MAX_TITLE_BOOSTS = 3
_TITLE_COMPONENT_MAX_WORDS = 4
_FUZZY_CUTOFF = 0.85
_FUZZY_MIN_ENTITY_LEN = 5

_title_prefixes: list[str] | None = None
_title_word_prefix_index: dict[str, list[str]] | None = None
_title_cache_built_at: float = 0.0
_title_prefixes_lock = threading.Lock()

_QUESTION_STARTERS = {
    "how", "what", "why", "where", "when", "is", "are", "does",
    "can", "will", "which", "who", "whose", "whom", "do", "did",
    "has", "have", "had", "was", "were", "should", "would", "could",
    "tell", "explain", "describe", "list", "show",
}


# ── Qdrant helpers ────────────────────────────────────────────────────────────

def _scroll_all(client: QdrantClient, collection: str, scroll_filter: Filter | None = None) -> list[dict]:
    """Scroll all matching points, auto-paginating."""
    records, offset = client.scroll(
        collection_name=collection,
        scroll_filter=scroll_filter,
        limit=1000,
        with_payload=True,
        with_vectors=False,
    )
    results = list(records)
    while offset is not None:
        records, offset = client.scroll(
            collection_name=collection,
            scroll_filter=scroll_filter,
            limit=1000,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        results.extend(records)
    return results


def _record_to_chunk(record, score: float) -> dict:
    p = record.payload or {}
    return {
        "text": p.get("text", ""),
        "page_title": p.get("page_title", ""),
        "section": p.get("section", ""),
        "url": p.get("url", ""),
        "score": score,
    }


def _scored_to_chunk(point) -> dict:
    p = point.payload or {}
    return {
        "text": p.get("text", ""),
        "page_title": p.get("page_title", ""),
        "section": p.get("section", ""),
        "url": p.get("url", ""),
        "score": round(point.score, 3),
    }


# ── Title cache ───────────────────────────────────────────────────────────────

def invalidate_title_cache() -> None:
    """Call after a wiki sync to force title index rebuild on next query."""
    global _title_prefixes, _title_word_prefix_index, _title_cache_built_at
    with _title_prefixes_lock:
        _title_prefixes = None
        _title_word_prefix_index = None
        _title_cache_built_at = 0.0
    logger.info("Title cache invalidated")


def _build_title_caches(client: QdrantClient, collection: str) -> None:
    global _title_prefixes, _title_word_prefix_index, _title_cache_built_at
    records = _scroll_all(client, collection)
    prefixes: set[str] = set()
    word_index: dict[str, set[str]] = {}
    for rec in records:
        title = (rec.payload or {}).get("page_title", "")
        if not title:
            continue
        if "/" in title:
            prefixes.add(title.rsplit("/", 1)[0] + "/")
        last = title.rsplit("/", 1)[-1]
        words = last.split()
        limit = min(len(words), _TITLE_COMPONENT_MAX_WORDS)
        for n in range(1, limit + 1):
            key = " ".join(words[:n]).lower()
            word_index.setdefault(key, set()).add(title)
    _title_prefixes = sorted(prefixes, key=len, reverse=True)
    _title_word_prefix_index = {k: sorted(v, key=lambda t: (len(t), t)) for k, v in word_index.items()}
    _title_cache_built_at = 1.0
    logger.debug("Built title caches: %d prefixes, %d word-prefix keys", len(_title_prefixes), len(_title_word_prefix_index))


def _get_title_prefixes(client: QdrantClient, collection: str) -> list[str]:
    global _title_prefixes
    if _title_prefixes is not None:
        return _title_prefixes
    with _title_prefixes_lock:
        if _title_prefixes is None:
            _build_title_caches(client, collection)
    return _title_prefixes  # type: ignore[return-value]


def _get_title_word_prefix_index(client: QdrantClient, collection: str) -> dict[str, list[str]]:
    global _title_word_prefix_index
    if _title_word_prefix_index is not None:
        return _title_word_prefix_index
    with _title_prefixes_lock:
        if _title_word_prefix_index is None:
            _build_title_caches(client, collection)
    return _title_word_prefix_index  # type: ignore[return-value]


# ── Category retrieval ────────────────────────────────────────────────────────

def _fetch_category_chunks(client: QdrantClient, collection: str, strategy: str) -> list[dict]:
    kind, value = strategy.split(":", 1)

    if kind == "prefix":
        records = _scroll_all(
            client, collection,
            scroll_filter=Filter(must=[FieldCondition(key="chunk_type", match=MatchValue(value="summary"))]),
        )
        chunks = [
            _record_to_chunk(r, 1.0) for r in records
            if not is_blocked(r.payload.get("page_title", ""))
            and value in (r.payload.get("page_path_segments") or [])
        ]

    elif kind == "category":
        records = _scroll_all(client, collection)
        tagged: set[str] = set()
        for r in records:
            if value in (r.payload.get("page_categories") or []):
                tagged.add(r.payload.get("page_title", ""))
        if not tagged:
            return []
        summary_records = _scroll_all(
            client, collection,
            scroll_filter=Filter(must=[FieldCondition(key="chunk_type", match=MatchValue(value="summary"))]),
        )
        chunks = [
            _record_to_chunk(r, 1.0) for r in summary_records
            if r.payload.get("page_title", "") in tagged
            and not is_blocked(r.payload.get("page_title", ""))
        ]

    elif kind == "allcontent":
        records = _scroll_all(
            client, collection,
            scroll_filter=Filter(must=[FieldCondition(key="text", match=MatchText(text=value))]),
        )
        chunks = [
            _record_to_chunk(r, 1.0) for r in records
            if not is_blocked(r.payload.get("page_title", ""))
        ]

    else:  # content:
        records = _scroll_all(
            client, collection,
            scroll_filter=Filter(must=[
                FieldCondition(key="chunk_type", match=MatchValue(value="summary")),
                FieldCondition(key="text", match=MatchText(text=value)),
            ]),
        )
        chunks = [
            _record_to_chunk(r, 1.0) for r in records
            if not is_blocked(r.payload.get("page_title", ""))
        ]

    chunks.sort(key=lambda c: c["page_title"])
    return chunks


def _query_category(
    client: QdrantClient,
    collection: str,
    strategy: str,
    verbose: bool,
    max_enum_chars: int,
    max_comparative_chars: int,
) -> list[dict]:
    chunks = _fetch_category_chunks(client, collection, strategy)
    condensed = condense_chunks(chunks, verbose, max_enum_chars, max_comparative_chars)
    return append_truncation_notice(condensed, len(chunks), strategy, STRATEGY_NOUN)


# ── Page-title retrieval ──────────────────────────────────────────────────────

def _fetch_page_chunks(client: QdrantClient, collection: str, page_title: str, score: float) -> list[dict]:
    records = _scroll_all(
        client, collection,
        scroll_filter=Filter(must=[FieldCondition(key="page_title", match=MatchValue(value=page_title))]),
    )
    return [_record_to_chunk(r, score) for r in records]


def _fuzzy_match_page_title(client: QdrantClient, collection: str, entity: str) -> str | None:
    if len(entity) < _FUZZY_MIN_ENTITY_LEN:
        return None
    idx = _get_title_word_prefix_index(client, collection)
    seen_titles = {t for titles in idx.values() for t in titles}
    last_components: dict[str, list[str]] = {}
    for title in seen_titles:
        last = title.rsplit("/", 1)[-1].lower()
        last_components.setdefault(last, []).append(title)
    close = difflib.get_close_matches(entity.lower(), last_components.keys(), n=2, cutoff=_FUZZY_CUTOFF)
    if len(close) != 1:
        return None
    candidates = last_components[close[0]]
    if len(candidates) != 1:
        return None
    return candidates[0]


def _query_by_page_title(client: QdrantClient, collection: str, entity: str) -> list[dict]:
    titled = entity.title()
    candidates = [entity, titled]
    for prefix in _get_title_prefixes(client, collection):
        candidates.append(f"{prefix}{titled}")
    candidates = list(dict.fromkeys(candidates))

    for candidate in candidates:
        if is_blocked(candidate):
            continue
        chunks = _fetch_page_chunks(client, collection, candidate, score=1.0)
        if chunks:
            return chunks

    idx = _get_title_word_prefix_index(client, collection)
    unblocked = [t for t in idx.get(entity.lower(), []) if not is_blocked(t)]
    if len(unblocked) == 1:
        chunks = _fetch_page_chunks(client, collection, unblocked[0], score=1.0)
        if chunks:
            return chunks

    fuzzy = _fuzzy_match_page_title(client, collection, entity)
    if fuzzy and not is_blocked(fuzzy):
        chunks = _fetch_page_chunks(client, collection, fuzzy, score=1.0)
        if chunks:
            return chunks

    return []


# ── Text-contains search ──────────────────────────────────────────────────────

def _text_contains_search(
    client: QdrantClient, collection: str, entity: str, k: int, score: float, dynamic: bool = False,
) -> list[dict]:
    if len(entity) < 4:
        return []
    pattern = re.compile(rf"\b{re.escape(entity)}\b", re.IGNORECASE)
    records = _scroll_all(
        client, collection,
        scroll_filter=Filter(must=[FieldCondition(key="text", match=MatchText(text=entity))]),
    )
    matches: list[dict] = []
    for r in records:
        p = r.payload or {}
        page_title = p.get("page_title", "")
        if is_blocked(page_title):
            continue
        text = p.get("text", "")
        if not pattern.search(text):
            continue
        if dynamic:
            chunk_score = dynamic_score(page_title, p.get("section", ""), text, pattern, cap=score)
            if chunk_score is None:
                continue
        else:
            chunk_score = score
        matches.append({
            "text": text, "page_title": page_title,
            "section": p.get("section", ""), "url": p.get("url", ""),
            "score": chunk_score,
        })
    return sorted(matches, key=lambda c: c["score"], reverse=True)[:k]


def _single_token_fallback(client: QdrantClient, collection: str, entity: str, k: int) -> list[dict]:
    _ENTITY_ACTION_VERBS: frozenset[str] = frozenset({
        "spawn", "summon", "get", "make", "find", "kill", "give", "craft", "build",
    })
    tokens = [
        t for t in entity.split()
        if t.lower() not in _QUESTION_STARTERS
        and t.lower() not in _ENTITY_ACTION_VERBS
        and len(t) >= 4
        and t[:1].isupper()
    ]
    tokens.sort(key=len, reverse=True)
    for tok in tokens:
        hits = _text_contains_search(client, collection, tok, k=k, score=0.85)
        if hits:
            logger.info("Single-token fallback: %r → %d chunks via %r", entity, len(hits), tok)
            return hits
    return []


# ── Bare entity detection ─────────────────────────────────────────────────────

def _bare_entity_name(question: str) -> str | None:
    text = question.rstrip("?").strip()
    words = text.split()
    if not words or len(words) > 3:
        return None
    if words[0].lower() in _QUESTION_STARTERS:
        return None
    return text


def _find_title_hits_in_question(client: QdrantClient, collection: str, question: str) -> list[str]:
    words = question.rstrip("?").strip().split()
    content_words = [w for w in words if w.lower() not in _QUESTION_STARTERS]
    if not content_words:
        return []
    idx = _get_title_word_prefix_index(client, collection)
    hits: list[str] = []
    matched_words: set[str] = set()
    for size in (3, 2, 1):
        if len(hits) >= _MAX_TITLE_BOOSTS:
            break
        for i in range(len(content_words) - size + 1):
            if len(hits) >= _MAX_TITLE_BOOSTS:
                break
            phrase = " ".join(content_words[i:i + size]).lower()
            if len(phrase) < 3:
                continue
            phrase_words = set(phrase.split())
            if phrase_words <= matched_words:
                continue
            candidates = [t for t in idx.get(phrase, []) if not is_blocked(t) and t not in hits]
            if len(candidates) != 1:
                continue
            hits.append(candidates[0])
            matched_words.update(phrase_words)
    return hits


# ── Semantic search ───────────────────────────────────────────────────────────

def _semantic_search(
    client: QdrantClient,
    collection: str,
    variants: list[str],
    k: int,
    relevance_threshold: float,
    embed_model: str,
) -> list[dict]:
    seen: dict[str, dict] = {}
    for variant in variants:
        anchored = QUERY_PREFIX + variant
        vector = embed_one(anchored, embed_model)
        results = client.query_points(
            collection_name=collection,
            query=vector,
            limit=k,
            with_payload=True,
        )
        for point in results.points:
            p = point.payload or {}
            page_title = p.get("page_title", "")
            if is_blocked(page_title) or point.score < relevance_threshold:
                continue
            text = p.get("text", "")
            if text not in seen or point.score > seen[text]["score"]:
                seen[text] = {
                    "text": text, "page_title": page_title,
                    "section": p.get("section", ""), "url": p.get("url", ""),
                    "score": round(point.score, 3),
                }
    return sorted(seen.values(), key=lambda c: c["score"], reverse=True)[:k]


def _merge_results(direct: list[dict], semantic: list[dict], k: int) -> list[dict]:
    seen: dict[str, dict] = {}
    for c in list(direct) + list(semantic):
        text = c["text"]
        if text not in seen or c["score"] > seen[text]["score"]:
            seen[text] = c
    return sorted(seen.values(), key=lambda c: c["score"], reverse=True)[:k]


# ── Public entrypoint ─────────────────────────────────────────────────────────

def query(
    client: QdrantClient,
    collection: str,
    question: str,
    k_factual: int,
    k_comparative: int,
    relevance_threshold: float,
    embed_model: str,
    max_enum_chars: int,
    max_comparative_chars: int,
    expand_query_fn=None,
) -> list[dict]:
    """Embed the question, search Qdrant, return the most relevant wiki chunks.

    expand_query_fn: optional callable(question) -> list[str] for LLM query expansion.
    """
    question = normalize_query(question)

    if is_meta_question(question):
        logger.info("Meta-question — skipping retrieval: %r", question)
        return []

    comparative = is_comparative(question)
    k = k_comparative if comparative else k_factual

    enum_strategy = detect_enumeration(question)
    if not enum_strategy and comparative:
        enum_strategy = detect_category(question, exclude_broad=True)
    if enum_strategy:
        chunks = _query_category(client, collection, enum_strategy, verbose=comparative,
                                  max_enum_chars=max_enum_chars, max_comparative_chars=max_comparative_chars)
        if chunks:
            return chunks

    entity = _bare_entity_name(question)
    if entity:
        page_chunks = _query_by_page_title(client, collection, entity)
        if page_chunks:
            return page_chunks
        if len(entity.split()) == 1:
            cat = detect_category(question)
            if cat:
                cat_chunks = _query_category(client, collection, cat, verbose=False,
                                              max_enum_chars=max_enum_chars, max_comparative_chars=max_comparative_chars)
                if cat_chunks:
                    return cat_chunks
        text_hits = _text_contains_search(client, collection, entity, k=k_factual, score=0.9, dynamic=True)
        if text_hits:
            return text_hits
        if " " in entity:
            token_hits = _single_token_fallback(client, collection, entity, k=k_factual)
            if token_hits:
                return token_hits

    direct_chunks: list[dict] = []
    for page_title in _find_title_hits_in_question(client, collection, question):
        direct_chunks.extend(_fetch_page_chunks(client, collection, page_title, score=0.95))

    semantic_question = expand_abbreviations(question)
    variants = [semantic_question]
    if semantic_question != question:
        variants.insert(0, question)
    if expand_query_fn:
        variants.extend(expand_query_fn(semantic_question))
    if entity and entity not in variants:
        variants.append(entity)

    semantic_chunks = _semantic_search(client, collection, variants, k, relevance_threshold, embed_model)
    results = _merge_results(direct_chunks, semantic_chunks, k) if direct_chunks else semantic_chunks

    if not results and entity:
        text_hits = _text_contains_search(client, collection, entity, k=k, score=0.5)
        if text_hits:
            results = text_hits

    return results
