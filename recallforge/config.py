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
    dashscope_api_key: str = ""
    dashscope_endpoint: str = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
    dashscope_region: str = ""
    embedding_batch_size: int = 32
    embedding_batch_delay_seconds: float = 0.0
    embedding_requests_per_second: float = 0.0
    embedding_request_timeout_seconds: float = 60.0
    embedding_max_retries: int = 3

    # ── Reranker ──────────────────────────────────────────
    # When empty, reranker is disabled. Production requires reranker_required=True.
    reranker_model: str = ""
    reranker_provider: str = ""
    reranker_top_k: int = 50
    reranker_api_key: str = ""
    reranker_endpoint: str = ""
    reranker_request_timeout_seconds: float = 30.0
    reranker_max_retries: int = 3

    # ── Retrieval ─────────────────────────────────────────
    default_top_k: int = 50
    final_top_k: int = 8
    reranker_required: bool = True
    min_rerank_score: float = 0.35
    min_vector_score: float = 0.6
    min_top1_margin: float = 0.05
    max_context_tokens: int = 24000
    query_rewrite_enabled: bool = False
    hyde_enabled: bool = False
    min_query_length: int = 2
    parent_context_window_tokens: int = 2000
    search_mode: str = "vector"

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
