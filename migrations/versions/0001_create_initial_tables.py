"""create initial tables

Revision ID: 0001
Revises:
Create Date: 2026-05-13
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── rag_documents ──────────────────────────────────────────
    op.create_table(
        "rag_documents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=True),
        sa.Column("doc_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.CHAR(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("department", sa.Text(), nullable=False),
        sa.Column("access_level", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_rag_documents_version"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_rag_documents_content_hash"),
        sa.CheckConstraint(
            "access_level IN ('public', 'internal', 'confidential', 'restricted')",
            name="ck_rag_documents_access_level",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'deleted')",
            name="ck_rag_documents_status",
        ),
        sa.UniqueConstraint("tenant_id", "source_uri", "version", name="uq_rag_documents_source_version"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "uq_rag_documents_active_source",
        "rag_documents",
        ["tenant_id", "source_uri"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("idx_rag_documents_tenant_source", "rag_documents", ["tenant_id", "source_uri"])
    op.create_index("idx_rag_documents_tenant_status_doc_type", "rag_documents", ["tenant_id", "status", "doc_type"])
    op.create_index("idx_rag_documents_source_hash", "rag_documents", ["tenant_id", "source_uri", "content_hash"])

    # ── rag_parent_chunks ──────────────────────────────────────
    op.create_table(
        "rag_parent_chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.Text(), nullable=False),
        sa.Column("parent_key", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.CHAR(64), nullable=False),
        sa.Column("department", sa.Text(), nullable=False),
        sa.Column("access_level", sa.Text(), nullable=False),
        sa.Column("heading_path", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("chunk_index >= 0", name="ck_rag_parent_chunks_chunk_index"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_rag_parent_chunks_content_hash"),
        sa.CheckConstraint(
            "access_level IN ('public', 'internal', 'confidential', 'restricted')",
            name="ck_rag_parent_chunks_access_level",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'deleted')",
            name="ck_rag_parent_chunks_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_rag_parent_chunks_version"),
        sa.CheckConstraint(
            "page_start IS NULL OR page_end IS NULL OR page_end >= page_start",
            name="ck_rag_parent_chunks_pages",
        ),
        sa.CheckConstraint("page_start IS NULL OR page_start >= 1", name="ck_rag_parent_chunks_page_start"),
        sa.CheckConstraint("page_end IS NULL OR page_end >= 1", name="ck_rag_parent_chunks_page_end"),
        sa.CheckConstraint("token_count IS NULL OR token_count >= 0", name="ck_rag_parent_chunks_token_count"),
        sa.UniqueConstraint("document_id", "parent_key", name="uq_rag_parent_chunks_document_key"),
        sa.ForeignKeyConstraint(["document_id"], ["rag_documents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "idx_rag_parent_chunks_tenant_document_status",
        "rag_parent_chunks",
        ["tenant_id", "document_id", "status"],
    )
    op.create_index(
        "idx_rag_parent_chunks_tenant_status_version",
        "rag_parent_chunks",
        ["tenant_id", "status", "version"],
    )
    op.create_index(
        "idx_rag_parent_chunks_tenant_doc_type_status",
        "rag_parent_chunks",
        ["tenant_id", "doc_type", "status"],
    )
    op.create_index(
        "idx_rag_parent_chunks_active_version",
        "rag_parent_chunks",
        ["tenant_id", "source_uri", "version"],
        postgresql_where=sa.text("status = 'active'"),
    )

    # ── rag_chunks ─────────────────────────────────────────────
    op.create_table(
        "rag_chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_key", sa.Text(), nullable=False),
        sa.Column("parent_key", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.CHAR(64), nullable=False),
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple'::regconfig, coalesce(content, ''))", persisted=True),
            nullable=True,
        ),
        sa.Column("doc_type", sa.Text(), nullable=False),
        sa.Column("chunk_type", sa.Text(), server_default=sa.text("'child'"), nullable=False),
        sa.Column("template", sa.Text(), nullable=True),
        sa.Column("department", sa.Text(), nullable=False),
        sa.Column("access_level", sa.Text(), nullable=False),
        sa.Column("heading_path", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("embedding_provider", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column("embedding_text_embedding_v4_1024", Vector(1024), nullable=True),
        sa.Column("embedding_metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("chunk_index >= 0", name="ck_rag_chunks_chunk_index"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_rag_chunks_content_hash"),
        sa.CheckConstraint(
            "access_level IN ('public', 'internal', 'confidential', 'restricted')",
            name="ck_rag_chunks_access_level",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'deleted')",
            name="ck_rag_chunks_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_rag_chunks_version"),
        sa.CheckConstraint(
            "page_start IS NULL OR page_end IS NULL OR page_end >= page_start",
            name="ck_rag_chunks_pages",
        ),
        sa.CheckConstraint("page_start IS NULL OR page_start >= 1", name="ck_rag_chunks_page_start"),
        sa.CheckConstraint("page_end IS NULL OR page_end >= 1", name="ck_rag_chunks_page_end"),
        sa.CheckConstraint("chunk_type = 'child'", name="ck_rag_chunks_chunk_type"),
        sa.CheckConstraint("embedding_dim > 0", name="ck_rag_chunks_embedding_dim"),
        sa.UniqueConstraint("document_id", "chunk_key", name="uq_rag_chunks_document_key"),
        sa.ForeignKeyConstraint(["document_id"], ["rag_documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parent_id"], ["rag_parent_chunks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("idx_rag_chunks_tenant_doc_type", "rag_chunks", ["tenant_id", "doc_type"])
    op.create_index("idx_rag_chunks_document", "rag_chunks", ["document_id"])
    op.create_index("idx_rag_chunks_parent", "rag_chunks", ["parent_id"])
    op.create_index(
        "idx_rag_chunks_active_version",
        "rag_chunks",
        ["tenant_id", "source_uri", "version"],
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "idx_rag_chunks_permission_active",
        "rag_chunks",
        ["tenant_id", "department", "access_level", "doc_type", "status", "version"],
    )
    op.create_index(
        "idx_rag_chunks_embedding_model_active",
        "rag_chunks",
        ["tenant_id", "embedding_model", "status"],
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "idx_rag_chunks_content_tsv_active",
        "rag_chunks",
        ["content_tsv"],
        postgresql_using="gin",
        postgresql_where=sa.text("status = 'active'"),
    )

    # ── rag_ingest_jobs ────────────────────────────────────────
    op.create_table(
        "rag_ingest_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", postgresql.UUID(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=True),
        sa.Column("doc_type", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.CHAR(64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("parser", sa.Text(), nullable=True),
        sa.Column("template", sa.Text(), nullable=True),
        sa.Column("parser_used", sa.Text(), nullable=True),
        sa.Column("chunker_used", sa.Text(), nullable=True),
        sa.Column("parent_chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("child_chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("warnings", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("parse_report", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed', 'skipped_duplicate')",
            name="ck_rag_ingest_jobs_status",
        ),
        sa.CheckConstraint(
            "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_rag_ingest_jobs_content_hash",
        ),
        sa.CheckConstraint("version IS NULL OR version >= 1", name="ck_rag_ingest_jobs_version"),
        sa.CheckConstraint("parent_chunk_count >= 0", name="ck_rag_ingest_jobs_parent_chunk_count"),
        sa.CheckConstraint("child_chunk_count >= 0", name="ck_rag_ingest_jobs_child_chunk_count"),
        sa.UniqueConstraint("job_id", name="uq_rag_ingest_jobs_job_id"),
        sa.UniqueConstraint("tenant_id", "job_id", name="uq_rag_ingest_jobs_tenant_job_id"),
        sa.ForeignKeyConstraint(["document_id"], ["rag_documents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "idx_rag_ingest_jobs_tenant_status_created",
        "rag_ingest_jobs",
        ["tenant_id", "status", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_rag_ingest_jobs_tenant_source_created",
        "rag_ingest_jobs",
        ["tenant_id", "source_uri", sa.text("created_at DESC")],
    )
    op.create_index("idx_rag_ingest_jobs_document", "rag_ingest_jobs", ["document_id"])

    # ── rag_query_logs ─────────────────────────────────────────
    op.create_table(
        "rag_query_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("request_id", postgresql.UUID(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("department", sa.Text(), nullable=False),
        sa.Column("access_level", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("rewritten_query", sa.Text(), nullable=True),
        sa.Column("filters", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("client_filters", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("search_mode", sa.Text(), nullable=False),
        sa.Column("embedding_provider", sa.Text(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("reranker_provider", sa.Text(), nullable=True),
        sa.Column("reranker_model", sa.Text(), nullable=True),
        sa.Column("top_k", sa.Integer(), nullable=True),
        sa.Column("final_top_k", sa.Integer(), nullable=True),
        sa.Column("min_rerank_score", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("min_top1_margin", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("max_context_tokens", sa.Integer(), nullable=True),
        sa.Column("hit_summary", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("selected_references", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("refusal_reason", sa.Text(), nullable=True),
        sa.Column("latencies_ms", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "access_level IN ('public', 'internal', 'confidential', 'restricted')",
            name="ck_rag_query_logs_access_level",
        ),
        sa.CheckConstraint(
            "status IN ('success', 'refused', 'failed')",
            name="ck_rag_query_logs_status",
        ),
        sa.CheckConstraint(
            "search_mode IN ('vector', 'full_text', 'hybrid')",
            name="ck_rag_query_logs_search_mode",
        ),
        sa.CheckConstraint("embedding_dim IS NULL OR embedding_dim > 0", name="ck_rag_query_logs_embedding_dim"),
        sa.CheckConstraint("top_k IS NULL OR top_k > 0", name="ck_rag_query_logs_top_k"),
        sa.CheckConstraint("final_top_k IS NULL OR final_top_k > 0", name="ck_rag_query_logs_final_top_k"),
        sa.CheckConstraint(
            "max_context_tokens IS NULL OR max_context_tokens > 0",
            name="ck_rag_query_logs_max_context_tokens",
        ),
        sa.CheckConstraint(
            "(status = 'success' AND answer IS NOT NULL) "
            "OR (status = 'refused' AND refusal_reason IS NOT NULL) "
            "OR (status = 'failed' AND error_message IS NOT NULL)",
            name="ck_rag_query_logs_status_payload",
        ),
        sa.CheckConstraint(
            "search_mode = 'full_text' "
            "OR (embedding_provider IS NOT NULL AND embedding_model IS NOT NULL AND embedding_dim IS NOT NULL)",
            name="ck_rag_query_logs_vector_embedding",
        ),
        sa.UniqueConstraint("request_id", name="uq_rag_query_logs_request_id"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "idx_rag_query_logs_tenant_created",
        "rag_query_logs",
        ["tenant_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_rag_query_logs_tenant_user_created",
        "rag_query_logs",
        ["tenant_id", "user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_rag_query_logs_status_created",
        "rag_query_logs",
        ["status", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_rag_query_logs_tenant_status_created",
        "rag_query_logs",
        ["tenant_id", "status", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_table("rag_query_logs")
    op.drop_table("rag_ingest_jobs")
    op.drop_table("rag_chunks")
    op.drop_table("rag_parent_chunks")
    op.drop_table("rag_documents")
    # NOTE: We intentionally do NOT drop the 'vector' extension here.
    # Other schemas in the same database may depend on it.
    # If you truly need to remove it, run manually:
    #   DROP EXTENSION IF EXISTS vector;
