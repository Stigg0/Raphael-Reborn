"""Groq API provider — 4-model fallback chain."""
import re

from groq import APIStatusError, Groq, RateLimitError

PRIMARY_MODEL = "llama-3.3-70b-versatile"
FALLBACK_MODEL = "qwen/qwen3-32b"
TERTIARY_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
LAST_RESORT_MODEL = "llama-3.1-8b-instant"
MODEL_CHAIN: list[str] = [PRIMARY_MODEL, FALLBACK_MODEL, TERTIARY_MODEL, LAST_RESORT_MODEL]

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_UNCLOSED_RE = re.compile(r"<think>.*", re.DOTALL)
_MD_HEADER_RE = re.compile(r"^#{1,4}\s+", re.MULTILINE)
_HR_RE = re.compile(r"^-{3,}\s*$", re.MULTILINE)


def _strip_model_artifacts(content: str) -> str:
    content = _THINK_RE.sub("", content)
    content = _THINK_UNCLOSED_RE.sub("", content).strip()
    content = _MD_HEADER_RE.sub("", content)
    content = _HR_RE.sub("", content)
    return re.sub(r"\n{3,}", "\n\n", content).strip()


class GroqProvider:
    def __init__(self, api_key: str) -> None:
        self._client = Groq(api_key=api_key, max_retries=0)

    @property
    def model_id(self) -> str:
        return f"groq/{PRIMARY_MODEL}"

    def _call(self, model: str, system_prompt: str, user_message: str, max_tokens: int, temperature: float) -> str:
        response = self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if not response.choices:
            return ""
        content = response.choices[0].message.content or ""
        return _strip_model_artifacts(content)

    def complete(self, system_prompt: str, user_message: str, max_tokens: int = 1024, temperature: float = 0.1) -> str:
        """Try MODEL_CHAIN in order, falling back on rate-limit or payload errors."""
        for i, model in enumerate(MODEL_CHAIN):
            try:
                result = self._call(model, system_prompt, user_message, max_tokens, temperature)
                return result or ""
            except RateLimitError:
                if i + 1 < len(MODEL_CHAIN):
                    continue
                raise
            except APIStatusError as e:
                if e.status_code == 413 and i + 1 < len(MODEL_CHAIN):
                    continue
                raise
        return ""

    def complete_simple(self, system_prompt: str, user_message: str, max_tokens: int = 80, temperature: float = 0.3) -> str:
        try:
            return self._call(PRIMARY_MODEL, system_prompt, user_message, max_tokens, temperature)
        except Exception:
            return ""
