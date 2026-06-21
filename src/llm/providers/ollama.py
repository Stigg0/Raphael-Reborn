"""Ollama provider — OpenAI-compatible local LLM via HTTP."""
import re

import httpx

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip(content: str) -> str:
    content = _THINK_RE.sub("", content).strip()
    return re.sub(r"\n{3,}", "\n\n", content).strip()


class OllamaProvider:
    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

    @property
    def model_id(self) -> str:
        return f"ollama/{self._model}"

    def _call(self, system_prompt: str, user_message: str, max_tokens: int, temperature: float) -> str:
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "options": {"num_predict": max_tokens, "temperature": temperature},
                    "stream": False,
                },
            )
            resp.raise_for_status()
        return _strip(resp.json().get("message", {}).get("content", ""))

    def complete(self, system_prompt: str, user_message: str, max_tokens: int = 1024, temperature: float = 0.1) -> str:
        return self._call(system_prompt, user_message, max_tokens, temperature)

    def complete_simple(self, system_prompt: str, user_message: str, max_tokens: int = 80, temperature: float = 0.3) -> str:
        try:
            return self._call(system_prompt, user_message, max_tokens, temperature)
        except Exception:
            return ""
