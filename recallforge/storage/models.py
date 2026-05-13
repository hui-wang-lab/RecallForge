"""SQLAlchemy 2.0 models for RecallForge M1 data foundation."""

from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Column,
    Computed,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import TSVECTOR as _TSVECTOR
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# ── Enum constant sets ──────────────────────────────────────────

ACCESS_LEVELS = ("public", "internal", "confidential", "restricted")
DOCUMENT_STATUSES = ("active", "superseded", "deleted")
INGEST_STATUSES = ("pending", "running", "success", "failed", "skipped_duplicate")
QUERY_STATUSES = ("success", "refused", "failed")
SEARCH_MODES = ("vector", "full_text", "hybrid")


# ── rag_documents ──────────────────────────────────────────────


class RagDocument(Base):
    __tablename__ = "rag_documents"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(Text, nullable=False)
    source_uri = Column(Text, nullable=False)
    source_name = Column(Text)
    doc_type = Column(Text, nullable=False)
    title = Column(Text)
    content_hash = Column(CHAR(64), nullable=False)
    version = Column(Integer, nullable=False)
    status = Column(Text, nullable=False)
    department = Column(Text, nullable=False)
    access_level = Column(Text, nullable=False)
    metadata_ = Column("metadata", JSONB, nullable=False, server_default="'{}'::jsonb")
    created_by = Column(Text)
    updated_by = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
    deleted_at = Column(TIMESTAMP(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "version >= 1",
            name="ck_rag_documents_version",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_rag_documents_content_hash",
        ),
        CheckConstraint(
            f"access_level IN {ACCESS_LEVELS}",
            name="ck_rag_documents_access_level",
        ),
        CheckConstraint(
            f"status IN {DOCUMENT_STATUSES}",
            name="ck_rag_documents_status",
        ),
        UniqueConstraint(
            "tenant_id", "source_uri", "version",
            name="uq_rag_documents_source_version",
        ),
        Index(
            "uq_rag_documents_active_source",
            "tenant_id", "source_uri",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "idx_rag_documents_tenant_source",
            "tenant_id", "source_uri",
        ),
        Index(
            "idx_rag_documents_tenant_status_doc_type",
            "tenant_id", "status", "doc_type",
        ),
        Index(
            "idx_rag_documents_source_hash",
            "tenant_id", "source_uri", "content_hash",
        ),
    )


# ── rag_parent_chunks ──────────────────────────────────────────


class RagParentChunk(Base):
    __tablename__ = "rag_parent_chunks"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(Text, nullable=False)
    document_id = Column(
        BigInteger,
        ForeignKey("rag_documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_uri = Column(Text, nullable=False)
    doc_type = Column(Text, nullable=False)
    parent_key = Column(Text, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    content_hash = Column(CHAR(64), nullable=False)
    department = Column(Text, nullable=False)
    access_level = Column(Text, nullable=False)
    heading_path = Column(ARRAY(Text))
    page_start = Column(Integer)
    page_end = Column(Integer)
    token_count = Column(Integer)
    status = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    metadata_ = Column("metadata", JSONB, nullable=False, server_default="'{}'::jsonb")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
    deleted_at = Column(TIMESTAMP(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "chunk_index >= 0",
            name="ck_rag_parent_chunks_chunk_index",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_rag_parent_chunks_content_hash",
        ),
        CheckConstraint(
            f"access_level IN {ACCESS_LEVELS}",
            name="ck_rag_parent_chunks_access_level",
        ),
        CheckConstraint(
            f"status IN {DOCUMENT_STATUSES}",
            name="ck_rag_parent_chunks_status",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_rag_parent_chunks_version",
        ),
        CheckConstraint(
            "page_start IS NULL OR page_end IS NULL OR page_end >= page_start",
            name="ck_rag_parent_chunks_pages",
        ),
        CheckConstraint(
            "page_start IS NULL OR page_start >= 1",
            name="ck_rag_parent_chunks_page_start",
        ),
        CheckConstraint(
            "page_end IS NULL OR page_end >= 1",
            name="ck_rag_parent_chunks_page_end",
        ),
        CheckConstraint(
            "token_count IS NULL OR token_count >= 0",
            name="ck_rag_parent_chunks_token_count",
        ),
        UniqueConstraint(
            "document_id", "parent_key",
            name="uq_rag_parent_chunks_document_key",
        ),
        Index(
            "idx_rag_parent_chunks_tenant_document_status",
            "tenant_id", "document_id", "status",
        ),
        Index(
            "idx_rag_parent_chunks_tenant_status_version",
            "tenant_id", "status", "version",
        ),
        Index(
            "idx_rag_parent_chunks_tenant_doc_type_status",
            "tenant_id", "doc_type", "status",
        ),
        Index(
            "idx_rag_parent_chunks_active_version",
            "tenant_id", "source_uri", "version",
            postgresql_where=text("status = 'active'"),
        ),
    )


# ── rag_chunks ─────────────────────────────────────────────────


class RagChunk(Base):
    __tablename__ = "rag_chunks"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(Text, nullable=False)
    document_id = Column(
        BigInteger,
        ForeignKey("rag_documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    parent_id = Column(
        BigInteger,
        ForeignKey("rag_parent_chunks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    chunk_key = Column(Text, nullable=False)
    parent_key = Column(Text, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    content_hash = Column(CHAR(64), nullable=False)
    content_tsv = Column(
        _TSVECTOR,
        Computed("to_tsvector('simple'::regconfig, coalesce(content, ''))", persisted=True),
        nullable=True,
    )
    doc_type = Column(Text, nullable=False)
    chunk_type = Column(Text, nullable=False, server_default="'child'")
    template = Column(Text)
    department = Column(Text, nullable=False)
    access_level = Column(Text, nullable=False)
    heading_path = Column(ARRAY(Text))
    page_start = Column(Integer)
    page_end = Column(Integer)
    source_uri = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    status = Column(Text, nullable=False)
    embedding_provider = Column(Text, nullable=False)
    embedding_model = Column(Text, nullable=False)
    embedding_dim = Column(Integer, nullable=False)
    embedding_text_embedding_v4_1024 = Column(Vector(1024), nullable=True)
    embedding_metadata = Column(JSONB, nullable=False, server_default="'{}'::jsonb")
    metadata_ = Column("metadata", JSONB, nullable=False, server_default="'{}'::jsonb")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
    deleted_at = Column(TIMESTAMP(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "chunk_index >= 0",
            name="ck_rag_chunks_chunk_index",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_rag_chunks_content_hash",
        ),
        CheckConstraint(
            f"access_level IN {ACCESS_LEVELS}",
            name="ck_rag_chunks_access_level",
        ),
        CheckConstraint(
            f"status IN {DOCUMENT_STATUSES}",
            name="ck_rag_chunks_status",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_rag_chunks_version",
        ),
        CheckConstraint(
            "page_start IS NULL OR page_end IS NULL OR page_end >= page_start",
            name="ck_rag_chunks_pages",
        ),
        CheckConstraint(
            "page_start IS NULL OR page_start >= 1",
            name="ck_rag_chunks_page_start",
        ),
        CheckConstraint(
            "page_end IS NULL OR page_end >= 1",
            name="ck_rag_chunks_page_end",
        ),
        CheckConstraint(
            "chunk_type = 'child'",
            name="ck_rag_chunks_chunk_type",
        ),
        CheckConstraint(
            "embedding_dim > 0",
            name="ck_rag_chunks_embedding_dim",
        ),
        UniqueConstraint(
            "document_id", "chunk_key",
            name="uq_rag_chunks_document_key",
        ),
        Index(
            "idx_rag_chunks_tenant_doc_type",
            "tenant_id", "doc_type",
        ),
        Index(
            "idx_rag_chunks_document",
            "document_id",
        ),
        Index(
            "idx_rag_chunks_parent",
            "parent_id",
        ),
        Index(
            "idx_rag_chunks_active_version",
            "tenant_id", "source_uri", "version",
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "idx_rag_chunks_permission_active",
            "tenant_id", "department", "access_level", "doc_type", "status", "version",
        ),
        Index(
            "idx_rag_chunks_embedding_model_active",
            "tenant_id", "embedding_model", "status",
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "idx_rag_chunks_content_tsv_active",
            "content_tsv",
            postgresql_using="gin",
            postgresql_where=text("status = 'active'"),
        ),
    )


# ── rag_ingest_jobs ────────────────────────────────────────────


class RagIngestJob(Base):
    __tablename__ = "rag_ingest_jobs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(Uuid, nullable=False)
    tenant_id = Column(Text, nullable=False)
    document_id = Column(
        BigInteger,
        ForeignKey("rag_documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_uri = Column(Text, nullable=False)
    source_name = Column(Text)
    doc_type = Column(Text)
    status = Column(Text, nullable=False)
    content_hash = Column(CHAR(64))
    version = Column(Integer)
    parser = Column(Text)
    template = Column(Text)
    parser_used = Column(Text)
    chunker_used = Column(Text)
    parent_chunk_count = Column(Integer, nullable=False, server_default="0")
    child_chunk_count = Column(Integer, nullable=False, server_default="0")
    warnings = Column(JSONB, nullable=False, server_default="'[]'::jsonb")
    parse_report = Column(JSONB, nullable=False, server_default="'{}'::jsonb")
    error_message = Column(Text)
    metadata_ = Column("metadata", JSONB, nullable=False, server_default="'{}'::jsonb")
    created_by = Column(Text)
    started_at = Column(TIMESTAMP(timezone=True))
    finished_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")

    __table_args__ = (
        CheckConstraint(
            f"status IN {INGEST_STATUSES}",
            name="ck_rag_ingest_jobs_status",
        ),
        CheckConstraint(
            "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_rag_ingest_jobs_content_hash",
        ),
        CheckConstraint(
            "version IS NULL OR version >= 1",
            name="ck_rag_ingest_jobs_version",
        ),
        CheckConstraint(
            "parent_chunk_count >= 0",
            name="ck_rag_ingest_jobs_parent_chunk_count",
        ),
        CheckConstraint(
            "child_chunk_count >= 0",
            name="ck_rag_ingest_jobs_child_chunk_count",
        ),
        UniqueConstraint(
            "job_id",
            name="uq_rag_ingest_jobs_job_id",
        ),
        UniqueConstraint(
            "tenant_id", "job_id",
            name="uq_rag_ingest_jobs_tenant_job_id",
        ),
        Index(
            "idx_rag_ingest_jobs_tenant_status_created",
            "tenant_id", "status", text("created_at DESC"),
        ),
        Index(
            "idx_rag_ingest_jobs_tenant_source_created",
            "tenant_id", "source_uri", text("created_at DESC"),
        ),
        Index(
            "idx_rag_ingest_jobs_document",
            "document_id",
        ),
    )


# ── rag_query_logs ─────────────────────────────────────────────


class RagQueryLog(Base):
    __tablename__ = "rag_query_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    request_id = Column(Uuid, nullable=False, default=uuid.uuid4)
    tenant_id = Column(Text, nullable=False)
    user_id = Column(Text, nullable=False)
    department = Column(Text, nullable=False)
    access_level = Column(Text, nullable=False)
    question = Column(Text, nullable=False)
    rewritten_query = Column(Text)
    filters = Column(JSONB, nullable=False, server_default="'{}'::jsonb")
    client_filters = Column(JSONB, nullable=False, server_default="'{}'::jsonb")
    search_mode = Column(Text, nullable=False)
    embedding_provider = Column(Text)
    embedding_model = Column(Text)
    embedding_dim = Column(Integer)
    reranker_provider = Column(Text)
    reranker_model = Column(Text)
    top_k = Column(Integer)
    final_top_k = Column(Integer)
    min_rerank_score = Column(Numeric(6, 4))
    min_top1_margin = Column(Numeric(6, 4))
    max_context_tokens = Column(Integer)
    hit_summary = Column(JSONB, nullable=False, server_default="'[]'::jsonb")
    selected_references = Column(JSONB, nullable=False, server_default="'[]'::jsonb")
    answer = Column(Text)
    refusal_reason = Column(Text)
    latencies_ms = Column(JSONB, nullable=False, server_default="'{}'::jsonb")
    metadata_ = Column("metadata", JSONB, nullable=False, server_default="'{}'::jsonb")
    status = Column(Text, nullable=False)
    error_message = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")

    __table_args__ = (
        CheckConstraint(
            f"access_level IN {ACCESS_LEVELS}",
            name="ck_rag_query_logs_access_level",
        ),
        CheckConstraint(
            f"status IN {QUERY_STATUSES}",
            name="ck_rag_query_logs_status",
        ),
        CheckConstraint(
            f"search_mode IN {SEARCH_MODES}",
            name="ck_rag_query_logs_search_mode",
        ),
        UniqueConstraint(
            "request_id",
            name="uq_rag_query_logs_request_id",
        ),
        CheckConstraint(
            "embedding_dim IS NULL OR embedding_dim > 0",
            name="ck_rag_query_logs_embedding_dim",
        ),
        CheckConstraint(
            "top_k IS NULL OR top_k > 0",
            name="ck_rag_query_logs_top_k",
        ),
        CheckConstraint(
            "final_top_k IS NULL OR final_top_k > 0",
            name="ck_rag_query_logs_final_top_k",
        ),
        CheckConstraint(
            "max_context_tokens IS NULL OR max_context_tokens > 0",
            name="ck_rag_query_logs_max_context_tokens",
        ),
        CheckConstraint(
            "(status = 'success' AND answer IS NOT NULL) "
            "OR (status = 'refused' AND refusal_reason IS NOT NULL) "
            "OR (status = 'failed' AND error_message IS NOT NULL)",
            name="ck_rag_query_logs_status_payload",
        ),
        CheckConstraint(
            "search_mode = 'full_text' "
            "OR (embedding_provider IS NOT NULL AND embedding_model IS NOT NULL AND embedding_dim IS NOT NULL)",
            name="ck_rag_query_logs_vector_embedding",
        ),
        Index(
            "idx_rag_query_logs_tenant_created",
            "tenant_id", text("created_at DESC"),
        ),
        Index(
            "idx_rag_query_logs_tenant_user_created",
            "tenant_id", "user_id", text("created_at DESC"),
        ),
        Index(
            "idx_rag_query_logs_status_created",
            "status", text("created_at DESC"),
        ),
        Index(
            "idx_rag_query_logs_tenant_status_created",
            "tenant_id", "status", text("created_at DESC"),
        ),
    )
