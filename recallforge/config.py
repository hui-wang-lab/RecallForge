from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────
    database_url: str = "postgresql://localhost:5432/recallforge"

    # ── Embedding ─────────────────────────────────────────
    embedding_model: str = "text-embedding-v4@1024"
    embedding_dim: int = 1024
    embedding_provider: str = "dashscope"

    # ── API Keys ──────────────────────────────────────────
    # Primary API key used for both the embedding provider and the LLM.
    # Named "openai_api_key" for broad compatibility; maps from env OPENAI_API_KEY.
    openai_api_key: str = ""
    openai_base_url: str = ""

    # ── Reranker ──────────────────────────────────────────
    # When empty, reranker is disabled. M4+ requires explicit configuration;
    # leaving these blank after M4 will log a warning on every query.
    reranker_model: str = ""
    reranker_provider: str = ""

    # ── Retrieval ─────────────────────────────────────────
    default_top_k: int = 30
    final_top_k: int = 8

    # ── Chunking ──────────────────────────────────────────
    child_max_tokens: int = 450
    child_min_tokens: int = 80
    parent_granularity: str = "chapter"
    ingest_max_file_bytes: int = 100 * 1024 * 1024
    ingest_parse_timeout_seconds: int = 120
    ingest_max_child_chunks_per_document: int = 20_000

    # ── Observability ─────────────────────────────────────
    log_level: str = "INFO"

    @field_validator("openai_api_key")
    @classmethod
    def _warn_empty_api_key(cls, v: str) -> str:
        if not v:
            import warnings

            warnings.warn(
                "OPENAI_API_KEY is empty. Embedding and LLM calls will fail at runtime.",
                stacklevel=2,
            )
        return v


@lru_cache
def get_config() -> Settings:
    return Settings()
