"""LLM provider protocol — all providers must implement this interface."""
from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def model_id(self) -> str:
        """Human-readable identifier for logging."""
        ...

    def complete(self, system_prompt: str, user_message: str, max_tokens: int, temperature: float) -> str:
        """Return the model's text response, or raise on unrecoverable failure."""
        ...

    def complete_simple(self, system_prompt: str, user_message: str, max_tokens: int, temperature: float) -> str:
        """Lightweight completion for query expansion — may be the same as complete()."""
        ...
