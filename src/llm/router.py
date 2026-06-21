"""LLM router — wraps a provider with query expansion and the Raphael persona."""
import logging
import re
import threading
import time

from llm.base import LLMProvider
from llm.prompts import RAPHAEL_SYSTEM_PROMPT, _sanitize_chunk, build_rag_prompt

logger = logging.getLogger(__name__)

MAX_TOKENS = 1024
TEMPERATURE = 0.1
_EXPANSION_COOLDOWN = 60.0

_INSUFFICIENT_DATA = (
    "That datum cannot be verified from the information before me. "
    "Narrow the target, and I shall calculate again."
)
_API_OVERLOADED = (
    "My analytical processes are momentarily strained. "
    "Repeat your inquiry shortly — I shall resume calculations imminently."
)
_RETRIEVAL_META_RE = re.compile(
    r"\b(?:the\s+)?(?:retrieved|provided|given)\s+"
    r"(?:context|wiki context|documentation|chunks?)\s+"
    r"(?:does\s+not\s+|doesn't\s+|do\s+not\s+|contains?|mentions?|states?|specifies?)",
    re.IGNORECASE,
)
_META_SENTENCE_RE = re.compile(
    r"(?im)^.*\b(?:retrieved context|provided context|given context|"
    r"wiki context|provided documentation|retrieved documentation|chunks?|"
    r"(?:my|raphael's)\s+archives?)\b.*(?:\n|$)"
)
_OVERVIEW_RE = re.compile(
    r"\b(?:what\s+is|what\s+does|what's|explain|overview|tell\s+me\s+about|"
    r"is\s+.+\s+included|do\s+we\s+have)\b",
    re.IGNORECASE,
)
_TECHNICAL_DETAIL_RE = re.compile(
    r"\b(?:setup|configure|configuration|config|port|ports|udp|proxy|server|"
    r"install|installation|command|commands|example|examples|recipe|recipes|"
    r"craft|crafting|exact|stat|stats|value|values|version|versions|troubleshoot|"
    r"error|issue|issues|how\s+do\s+i|how\s+to)\b",
    re.IGNORECASE,
)
_OVERVIEW_DETAIL_SENTENCE_RE = re.compile(
    r"(?i)\b(?:press(?:ing)?\s+[`']?[A-Z][`']?|keybind|gui|settings|mute|unmute|"
    r"group chats?|ports?|udp|tcp|firewall|proxy|velocity|bungee(?:cord)?|"
    r"waterfall|configuration|config|install|server setup|docker)\b"
)
_OVERVIEW_FOOTER_RE = re.compile(
    r"(?im)^\s*(?:classification|assessment|conclusion|functionally)\s*:\s*.*$"
)
_WHAT_IS_RE = re.compile(
    r"\b(?:what\s+is|what's|what\s+does|tell\s+me\s+about|explain)\s+(.+?)(?:\?|$)",
    re.IGNORECASE,
)
_MODPACK_CONTEXT_RE = re.compile(
    r"\b(?:minecraft|modpack|modpacks?|mods?|modded|fabric|forge|quilt|"
    r"curseforge|modrinth|tensura|slime|rimuru|raphael|tr\s*beyond|"
    r"beyond\s+worlds|tensura\s*:?\s*reincarnated|dungeon|server|"
    r"wiki|craft|recipe|recipes|item|items|block|blocks|mob|mobs|"
    r"race|races|skill|skills|magic|spells?|biome|structure|boss|"
    r"simple\s+voice\s+chat|voice\s+chat|sodium|iris|trinkets?)\b",
    re.IGNORECASE,
)
_LOW_RAG_CONFIDENCE_FOR_WEB = 0.75
_INSUFFICIENT_RESPONSE_RE = re.compile(
    r"\b(?:insufficient\s+data|cannot\s+(?:be\s+)?(?:verify|verified|identify)|"
    r"not\s+enough\s+information|no\s+(?:available\s+)?data|unavailable|"
    r"verify\s+the\s+spelling|provide\s+(?:more|further)\s+context)\b",
    re.IGNORECASE,
)


class LLMRouter:
    """Provider-agnostic facade used by the worker pipeline."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider
        self._rate_limit_lock = threading.Lock()
        self._last_rate_limit: float = 0.0

    def _expansion_cooldown_active(self) -> bool:
        with self._rate_limit_lock:
            return time.monotonic() - self._last_rate_limit < _EXPANSION_COOLDOWN

    def _record_rate_limit(self) -> None:
        with self._rate_limit_lock:
            self._last_rate_limit = time.monotonic()

    def expand_query(self, question: str) -> list[str]:
        """Return 2-3 wiki-vocabulary rephrasing of the question."""
        if self._expansion_cooldown_active():
            logger.debug("Skipping query expansion — recent rate limit")
            return []
        content = self._provider.complete_simple(
            system_prompt="Output only search queries, one per line. No commentary.",
            user_message=(
                "You are helping search a Tensura Minecraft mod wiki.\n"
                f"User query: {_sanitize_chunk(question)}\n\n"
                "Write 2-3 alternative phrasings using wiki-style terminology "
                "(stat names, skill/ability names, game mechanics). "
                "One phrasing per line. No numbering, no explanation."
            ),
            max_tokens=80,
            temperature=0.3,
        )
        if not content:
            return []
        return [line.strip() for line in content.splitlines() if line.strip()][:3]

    def answer(
        self,
        question: str,
        wiki_chunks: list[dict],
        subtitle_persona_chunks: list[dict],
        history: list[tuple[str, str]] | None = None,
    ) -> tuple[str, str]:
        """Generate a Raphael-persona answer. Returns (response_text, model_id)."""
        self._set_web_search_allowed(question, wiki_chunks)
        user_message = build_rag_prompt(question, wiki_chunks, subtitle_persona_chunks, history)
        try:
            response = self._provider.complete(
                system_prompt=RAPHAEL_SYSTEM_PROMPT,
                user_message=user_message,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
            )
            if not response:
                return _INSUFFICIENT_DATA, self._provider.model_id
            if self._should_retry_with_web_search(question, response, wiki_chunks):
                logger.info("Retrying insufficient modpack-context answer with web_search fallback")
                self._set_web_search_allowed(question, wiki_chunks, force=True)
                response = self._provider.complete(
                    system_prompt=RAPHAEL_SYSTEM_PROMPT,
                    user_message=user_message,
                    max_tokens=MAX_TOKENS,
                    temperature=TEMPERATURE,
                ) or response
            return self._clean_response(response, question), self._provider.model_id
        except Exception as exc:
            err_name = type(exc).__name__
            if "RateLimit" in err_name:
                self._record_rate_limit()
            logger.exception("LLM call failed (%s)", err_name)
            return _API_OVERLOADED, "error"

    def _set_web_search_allowed(self, question: str, wiki_chunks: list[dict], force: bool = False) -> None:
        setter = getattr(self._provider, "set_web_search_allowed", None)
        if not setter:
            return
        in_modpack_context = bool(_MODPACK_CONTEXT_RE.search(question))
        high_confidence_local_match = any(
            c.get("score", 0.0) >= _LOW_RAG_CONFIDENCE_FOR_WEB for c in wiki_chunks
        )
        unknown_locally = (
            not wiki_chunks
            or all(c.get("score", 0.0) < _LOW_RAG_CONFIDENCE_FOR_WEB for c in wiki_chunks)
        )
        setter((in_modpack_context and (unknown_locally or force)) or (force and high_confidence_local_match))

    def _should_retry_with_web_search(self, question: str, response: str, wiki_chunks: list[dict]) -> bool:
        if not _INSUFFICIENT_RESPONSE_RE.search(response):
            return False
        setter = getattr(self._provider, "set_web_search_allowed", None)
        if not setter:
            return False
        high_confidence_local_match = any(
            c.get("score", 0.0) >= _LOW_RAG_CONFIDENCE_FOR_WEB for c in wiki_chunks
        )
        return bool(_MODPACK_CONTEXT_RE.search(question)) or high_confidence_local_match

    @staticmethod
    def _clean_response(response: str, question: str = "") -> str:
        """Remove retrieval-system narration that breaks the Raphael illusion."""
        cleaned = _META_SENTENCE_RE.sub("", response).strip()
        if _RETRIEVAL_META_RE.search(cleaned):
            cleaned = _RETRIEVAL_META_RE.sub("the available facts", cleaned)
        if _OVERVIEW_RE.search(question) and not _TECHNICAL_DETAIL_RE.search(question):
            cleaned = _OVERVIEW_FOOTER_RE.sub("", cleaned).strip()
            sentences = re.split(r"(?<=[.!?])\s+", cleaned)
            kept = [s for s in sentences if not _OVERVIEW_DETAIL_SENTENCE_RE.search(s)]
            if kept:
                cleaned = " ".join(kept[:2]).strip()
            subject_match = _WHAT_IS_RE.search(question)
            if subject_match:
                subject = subject_match.group(1).strip(" ?.")
                subject_words = [w for w in re.findall(r"[A-Za-z0-9]+", subject.lower()) if len(w) > 2]
                if subject_words and not any(w in cleaned.lower() for w in subject_words):
                    cleaned = f"**{subject.title()}**: {cleaned[0].lower() + cleaned[1:] if cleaned else ''}".strip()
        return cleaned or _INSUFFICIENT_DATA


def build_provider(
    provider_type: str,
    groq_api_key: str | None,
    ollama_base_url: str,
    ollama_model: str,
    openai_base_url: str = "",
    openai_model: str = "",
    openai_enable_web_search: bool = False,
    openai_web_search_max_results: int = 5,
) -> LLMProvider:
    if provider_type == "openai":
        from llm.providers.openai_compatible import OpenAICompatibleProvider
        return OpenAICompatibleProvider(
            base_url=openai_base_url or ollama_base_url,
            model=openai_model or ollama_model,
            enable_web_search=openai_enable_web_search,
            web_search_max_results=openai_web_search_max_results,
        )
    if provider_type == "ollama":
        from llm.providers.ollama import OllamaProvider
        return OllamaProvider(base_url=ollama_base_url, model=ollama_model)
    if not groq_api_key:
        raise RuntimeError("LLM_PROVIDER=groq but GROQ_API_KEY is not set")
    from llm.providers.groq import GroqProvider
    return GroqProvider(api_key=groq_api_key)
