from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # Discord
    discord_token: str = ""

    # NATS JetStream
    nats_url: str = "nats://nats:4222"
    nats_message_hmac_key: str = ""

    # Qdrant
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""

    # LLM
    llm_provider: Literal["groq", "ollama", "openai"] = "groq"
    groq_api_key: str | None = None
    openai_base_url: str = ""
    openai_model: str = ""
    openai_enable_web_search: bool = False
    openai_web_search_max_results: int = 5
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.2"

    # M2M API keys — "role:key,role:key,..."
    # Roles: sync, read, admin
    api_keys: str = ""

    # Wiki
    wiki_host: str = "tensura.wiki.gg"

    # Qdrant collection names
    wiki_collection: str = "wiki"
    subtitles_collection: str = "subtitles"
    addons_collection: str = "addons"

    # Add-on/modpack project pages to scrape in addition to the main wiki.
    # Comma-separated URLs. Modrinth is preferred for project metadata because
    # CurseForge frequently blocks containerized HTTP requests.
    addon_source_urls: str = "https://wiki.trbeyond.com/wiki,https://modrinth.com/mod/tensura-beyond-worlds"
    addon_mod_wiki_max_pages: int = 8

    # Embedding — "local" uses sentence-transformers in-process;
    # "api" calls {embed_base_url}/v1/embeddings (LM Studio, Ollama, OpenAI-compatible).
    # When embed_base_url is empty, ollama_base_url is used as the fallback.
    embed_provider: Literal["local", "api"] = "local"
    embed_base_url: str = ""
    embed_model: str = "all-MiniLM-L6-v2"

    # Reranker — cross-encoder model applied after initial vector retrieval.
    # Set to "" to disable reranking.
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank_top_k: int = 5

    # RAG tuning
    relevance_threshold: float = 0.30
    k_factual: int = 8
    k_comparative: int = 10
    max_enum_chars: int = 8000
    max_comparative_chars: int = 16000

    # Bot behaviour
    cooldown_seconds: int = 6
    history_ttl_seconds: int = 600
    max_history_pairs: int = 3
    short_response_channel_id: str = ""
    short_response_char_limit: int = 0



@lru_cache
def get_settings() -> Settings:
    return Settings()
