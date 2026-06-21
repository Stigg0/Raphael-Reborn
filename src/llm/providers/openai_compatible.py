"""OpenAI-compatible local LLM provider.

Use this for LM Studio and other local servers exposing /v1/chat/completions.
"""
import json
import logging
import re

import httpx

from llm.web_search import web_search

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_UNCLOSED_RE = re.compile(r"<think>.*", re.DOTALL)
_CHANNEL_MARKER_RE = re.compile(r"<\|?/?channel\|?>\s*(?:thought|analysis|final)?", re.IGNORECASE)
_WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Fallback search for Minecraft modpack/mod ecosystem information. Use this only "
            "when indexed Tensura/TRBeyond/modpack context is insufficient or stale, and the "
            "question is still clearly about the Minecraft modpack, its mods, or related docs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The concise search query to send to a web search engine.",
                }
            },
            "required": ["query"],
        },
    },
}


def _strip(content: str) -> str:
    content = _THINK_RE.sub("", content)
    content = _THINK_UNCLOSED_RE.sub("", content).strip()
    content = _CHANNEL_MARKER_RE.sub("", content).strip()
    return re.sub(r"\n{3,}", "\n\n", content).strip()


class OpenAICompatibleProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        enable_web_search: bool = False,
        web_search_max_results: int = 5,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._enable_web_search = enable_web_search
        self._web_search_allowed = enable_web_search
        self._web_search_max_results = web_search_max_results

    @property
    def model_id(self) -> str:
        return f"openai-compatible/{self._model}"

    def set_web_search_allowed(self, allowed: bool) -> None:
        self._web_search_allowed = self._enable_web_search and allowed

    def _post_chat(self, payload: dict) -> dict:
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    def _call(self, system_prompt: str, user_message: str, max_tokens: int, temperature: float) -> str:
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if self._web_search_allowed:
            payload["tools"] = [_WEB_SEARCH_TOOL]
            payload["tool_choice"] = "required"

        data = self._post_chat(payload)
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if self._web_search_allowed and tool_calls:
            messages.append(message)
            for tool_call in tool_calls:
                function = tool_call.get("function") or {}
                if function.get("name") != "web_search":
                    continue
                try:
                    args = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                query = str(args.get("query") or "").strip()
                logger.info("OpenAI-compatible model requested web_search: %r", query)
                result = web_search(query, self._web_search_max_results)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id"),
                    "name": "web_search",
                    "content": result,
                })

            data = self._post_chat({
                "model": self._model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            })
            choices = data.get("choices") or []
            if not choices:
                return ""
            message = choices[0].get("message") or {}

        return _strip(message.get("content") or "")

    def complete(self, system_prompt: str, user_message: str, max_tokens: int = 1024, temperature: float = 0.1) -> str:
        return self._call(system_prompt, user_message, max_tokens, temperature)

    def complete_simple(self, system_prompt: str, user_message: str, max_tokens: int = 80, temperature: float = 0.3) -> str:
        try:
            return self._call(system_prompt, user_message, max(max_tokens, 256), temperature)
        except Exception:
            return ""
