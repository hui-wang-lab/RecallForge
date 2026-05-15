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

    # ── M5 HTTP API ───────────────────────────────────────
    api_enabled: bool = True
    api_title: str = "RecallForge Knowledge API"
    api_jwt_issuer: str = ""
    api_jwt_audience: str = ""
    api_jwt_public_key: str = ""
    api_service_keys: str = ""
    api_require_auth: bool = True
    api_request_id_header: str = "X-Request-Id"
    api_cors_allowed_origins: str = ""
    api_docs_enabled: bool = False
    api_openapi_enabled: bool = True
    api_startup_preflight_enabled: bool = False

    # ── M5 Console and uploads ────────────────────────────
    console_enabled: bool = False
    upload_temp_dir: str = ".tmp/uploads"
    upload_cleanup_enabled: bool = True
    upload_startup_cleanup_enabled: bool = True
    upload_temp_ttl_seconds: int = 86_400
    auto_embedding_backfill_on_ingest: bool = True
    ingest_backfill_limit: int = 20_000

    # ── M6 Knowledge governance ───────────────────────────
    default_knowledge_base_name: str = "Default Knowledge Base"
    require_knowledge_base_scope: bool = True
    allow_implicit_all_accessible_kbs: bool = True
    max_knowledge_bases_per_query: int = 20
    kb_list_default_limit: int = 20
    document_list_default_limit: int = 50
    document_delete_vector_sync_required: bool = True
    kb_delete_requires_empty: bool = False
    reindex_max_documents_per_request: int = 1000
    audit_enabled: bool = True

    # ── M5 Answer generation ──────────────────────────────
    answer_generation_enabled: bool = False
    llm_provider: str = ""
    llm_model: str = ""
    llm_endpoint: str = ""
    llm_api_key: str = ""
    llm_request_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    answer_max_tokens: int = 2048
    answer_temperature: float = 0.0
    answer_validate_citations: bool = True
    answer_repair_invalid_citations: bool = True

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
