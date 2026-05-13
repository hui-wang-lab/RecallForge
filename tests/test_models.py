"""Tests for SQLAlchemy models defined in M1."""

from __future__ import annotations

import uuid

import pytest

from recallforge.storage.models import (
    ACCESS_LEVELS,
    DOCUMENT_STATUSES,
    INGEST_STATUSES,
    QUERY_STATUSES,
    SEARCH_MODES,
    Base,
    RagChunk,
    RagDocument,
    RagIngestJob,
    RagParentChunk,
    RagQueryLog,
)

# ── Helpers ─────────────────────────────────────────────────────


def _get_columns(table) -> set[str]:
    return {c.name for c in table.columns}


def _get_constraints(table, type_) -> list[str]:
    return [c.name for c in table.constraints if isinstance(c, type_)]


def _get_check_constraint_names(table) -> set[str]:
    from sqlalchemy import CheckConstraint

    return {c.name for c in table.constraints if isinstance(c, CheckConstraint)}


def _get_unique_constraint_names(table) -> set[str]:
    from sqlalchemy import UniqueConstraint

    return {c.name for c in table.constraints if isinstance(c, UniqueConstraint)}


def _get_index_names(table) -> set[str]:
    return {idx.name for idx in table.indexes}


# ── Model instantiation tests ───────────────────────────────────


class TestRagDocument:
    def test_tablename(self):
        assert RagDocument.__tablename__ == "rag_documents"

    def test_instantiation(self):
        doc = RagDocument(
            tenant_id="t1",
            source_uri="file:///test.md",
            doc_type="markdown",
            content_hash="a" * 64,
            version=1,
            status="active",
            department="global",
            access_level="public",
        )
        assert doc.tenant_id == "t1"
        assert doc.status == "active"

    def test_columns_exist(self):
        cols = _get_columns(RagDocument.__table__)
        expected = {
            "id", "tenant_id", "source_uri", "source_name", "doc_type",
            "title", "content_hash", "version", "status", "department",
            "access_level", "metadata", "created_by", "updated_by",
            "created_at", "updated_at", "deleted_at",
        }
        assert expected.issubset(cols)

    def test_check_constraints(self):
        checks = _get_check_constraint_names(RagDocument.__table__)
        assert "ck_rag_documents_access_level" in checks
        assert "ck_rag_documents_content_hash" in checks
        assert "ck_rag_documents_status" in checks
        assert "ck_rag_documents_version" in checks

    def test_unique_constraints(self):
        uniques = _get_unique_constraint_names(RagDocument.__table__)
        assert "uq_rag_documents_source_version" in uniques

    def test_active_source_partial_unique_index(self):
        names = _get_index_names(RagDocument.__table__)
        assert "uq_rag_documents_active_source" in names


class TestRagParentChunk:
    def test_tablename(self):
        assert RagParentChunk.__tablename__ == "rag_parent_chunks"

    def test_instantiation(self):
        chunk = RagParentChunk(
            tenant_id="t1",
            document_id=1,
            source_uri="file:///test.md",
            doc_type="markdown",
            parent_key="p1",
            chunk_index=0,
            content="hello",
            content_hash="b" * 64,
            department="global",
            access_level="public",
            status="active",
            version=1,
        )
        assert chunk.parent_key == "p1"

    def test_columns_exist(self):
        cols = _get_columns(RagParentChunk.__table__)
        expected = {
            "id", "tenant_id", "document_id", "source_uri", "doc_type",
            "parent_key", "chunk_index", "content", "content_hash",
            "department", "access_level", "heading_path", "page_start",
            "page_end", "token_count", "status", "version", "metadata",
            "created_at", "updated_at", "deleted_at",
        }
        assert expected.issubset(cols)

    def test_check_constraints(self):
        checks = _get_check_constraint_names(RagParentChunk.__table__)
        assert "ck_rag_parent_chunks_access_level" in checks
        assert "ck_rag_parent_chunks_content_hash" in checks
        assert "ck_rag_parent_chunks_pages" in checks

    def test_unique_constraints(self):
        uniques = _get_unique_constraint_names(RagParentChunk.__table__)
        assert "uq_rag_parent_chunks_document_key" in uniques


class TestRagChunk:
    def test_tablename(self):
        assert RagChunk.__tablename__ == "rag_chunks"

    def test_instantiation(self):
        chunk = RagChunk(
            tenant_id="t1",
            document_id=1,
            parent_id=1,
            chunk_key="c1",
            parent_key="p1",
            chunk_index=0,
            content="hello world",
            content_hash="c" * 64,
            doc_type="markdown",
            department="global",
            access_level="public",
            source_uri="file:///test.md",
            version=1,
            status="active",
            embedding_provider="dashscope",
            embedding_model="text-embedding-v4@1024",
            embedding_dim=1024,
        )
        assert chunk.chunk_key == "c1"

    def test_columns_exist(self):
        cols = _get_columns(RagChunk.__table__)
        expected = {
            "id", "tenant_id", "document_id", "parent_id", "chunk_key",
            "parent_key", "chunk_index", "content", "content_hash",
            "content_tsv", "doc_type", "chunk_type", "template",
            "department", "access_level", "heading_path", "page_start",
            "page_end", "source_uri", "version", "status",
            "embedding_provider", "embedding_model", "embedding_dim",
            "embedding_text_embedding_v4_1024", "embedding_metadata",
            "metadata", "created_at", "updated_at", "deleted_at",
        }
        assert expected.issubset(cols)

    def test_embedding_column_type(self):
        col = RagChunk.__table__.c.embedding_text_embedding_v4_1024
        assert col is not None
        # pgvector Vector type should report dim=1024
        assert col.type.dim == 1024

    def test_content_tsv_generated_column(self):
        col = RagChunk.__table__.c.content_tsv
        assert col is not None
        assert col.computed is not None
        assert col.computed.persisted

    def test_check_constraints(self):
        checks = _get_check_constraint_names(RagChunk.__table__)
        assert "ck_rag_chunks_access_level" in checks
        assert "ck_rag_chunks_chunk_type" in checks
        assert "ck_rag_chunks_embedding_dim" in checks

    def test_unique_constraints(self):
        uniques = _get_unique_constraint_names(RagChunk.__table__)
        assert "uq_rag_chunks_document_key" in uniques

    def test_gin_index_on_content_tsv(self):
        for idx in RagChunk.__table__.indexes:
            if idx.name == "idx_rag_chunks_content_tsv_active":
                assert idx.dialect_options.get("postgresql", {}).get("using") == "gin"
                break
        else:
            pytest.fail("GIN index on content_tsv not found")

    def test_permission_index_exists(self):
        names = _get_index_names(RagChunk.__table__)
        assert "idx_rag_chunks_permission_active" in names


class TestRagIngestJob:
    def test_tablename(self):
        assert RagIngestJob.__tablename__ == "rag_ingest_jobs"

    def test_instantiation(self):
        job = RagIngestJob(
            job_id=uuid.uuid4(),
            tenant_id="t1",
            source_uri="file:///test.md",
            status="pending",
        )
        assert job.status == "pending"

    def test_columns_exist(self):
        cols = _get_columns(RagIngestJob.__table__)
        expected = {
            "id", "job_id", "tenant_id", "document_id", "source_uri",
            "source_name", "doc_type", "status", "content_hash", "version",
            "parser", "template", "parser_used", "chunker_used",
            "parent_chunk_count", "child_chunk_count", "warnings",
            "parse_report", "error_message", "metadata", "created_by",
            "started_at", "finished_at", "created_at", "updated_at",
        }
        assert expected.issubset(cols)

    def test_job_id_is_uuid_not_null(self):
        col = RagIngestJob.__table__.c.job_id
        assert col is not None
        assert not col.nullable
        # No column-level unique; uniqueness enforced by named constraint
        assert not col.unique

    def test_unique_constraints(self):
        uniques = _get_unique_constraint_names(RagIngestJob.__table__)
        assert "uq_rag_ingest_jobs_job_id" in uniques
        assert "uq_rag_ingest_jobs_tenant_job_id" in uniques

    def test_no_anonymous_unique_on_job_id(self):
        for idx in RagIngestJob.__table__.indexes:
            is_job_id = idx.columns == [RagIngestJob.__table__.c.job_id]
            if is_job_id and idx.unique and idx.name != "uq_rag_ingest_jobs_job_id":
                pytest.fail("Found anonymous unique index on job_id column")

    def test_check_constraints(self):
        checks = _get_check_constraint_names(RagIngestJob.__table__)
        assert "ck_rag_ingest_jobs_status" in checks

    def test_has_internal_autoincrement_id(self):
        col = RagIngestJob.__table__.c.id
        assert col is not None
        assert col.autoincrement
        assert col.primary_key


class TestRagQueryLog:
    def test_tablename(self):
        assert RagQueryLog.__tablename__ == "rag_query_logs"

    def test_instantiation(self):
        log = RagQueryLog(
            request_id=uuid.uuid4(),
            tenant_id="t1",
            user_id="user1",
            department="eng",
            access_level="public",
            question="what is RAG?",
            search_mode="vector",
            status="success",
            answer="RAG is retrieval-augmented generation.",
        )
        assert log.question == "what is RAG?"

    def test_columns_exist(self):
        cols = _get_columns(RagQueryLog.__table__)
        expected = {
            "id", "request_id", "tenant_id", "user_id", "department",
            "access_level", "question", "rewritten_query", "filters",
            "client_filters", "search_mode", "embedding_provider",
            "embedding_model", "embedding_dim", "reranker_provider",
            "reranker_model", "top_k", "final_top_k", "min_rerank_score",
            "min_top1_margin", "max_context_tokens", "hit_summary",
            "selected_references", "answer", "refusal_reason",
            "latencies_ms", "metadata", "status", "error_message",
            "created_at",
        }
        assert expected.issubset(cols)

    def test_check_constraints(self):
        checks = _get_check_constraint_names(RagQueryLog.__table__)
        assert "ck_rag_query_logs_access_level" in checks
        assert "ck_rag_query_logs_status_payload" in checks
        assert "ck_rag_query_logs_vector_embedding" in checks

    def test_request_id_unique_constraint(self):
        uniques = _get_unique_constraint_names(RagQueryLog.__table__)
        assert "uq_rag_query_logs_request_id" in uniques

    def test_request_id_no_column_level_unique(self):
        col = RagQueryLog.__table__.c.request_id
        assert not col.unique


# ── Enum closure tests ──────────────────────────────────────────


class TestEnumClosure:
    def test_access_levels_closed(self):
        assert ACCESS_LEVELS == ("public", "internal", "confidential", "restricted")

    def test_document_statuses_closed(self):
        assert DOCUMENT_STATUSES == ("active", "superseded", "deleted")

    def test_ingest_statuses_closed(self):
        assert INGEST_STATUSES == ("pending", "running", "success", "failed", "skipped_duplicate")

    def test_query_statuses_closed(self):
        assert QUERY_STATUSES == ("success", "refused", "failed")

    def test_search_modes_closed(self):
        assert SEARCH_MODES == ("vector", "full_text", "hybrid")


# ── Cross-cutting: metadata_ mapping ────────────────────────────


class TestMetadataColumnMapping:
    """Verify that Python attribute 'metadata_' maps to DB column 'metadata'."""

    def test_document_metadata_column_name(self):
        assert RagDocument.__table__.c.metadata.name == "metadata"

    def test_parent_chunk_metadata_column_name(self):
        assert RagParentChunk.__table__.c.metadata.name == "metadata"

    def test_chunk_metadata_column_name(self):
        assert RagChunk.__table__.c.metadata.name == "metadata"

    def test_ingest_job_metadata_column_name(self):
        assert RagIngestJob.__table__.c.metadata.name == "metadata"

    def test_query_log_metadata_column_name(self):
        assert RagQueryLog.__table__.c.metadata.name == "metadata"


# ── Base metadata completeness ──────────────────────────────────


class TestBaseMetadata:
    def test_five_tables_registered(self):
        table_names = {t.name for t in Base.metadata.sorted_tables}
        expected = {
            "rag_documents",
            "rag_parent_chunks",
            "rag_chunks",
            "rag_ingest_jobs",
            "rag_query_logs",
        }
        assert table_names == expected

    def test_foreign_keys_reference_correct_tables(self):
        fk_targets = set()
        for table in Base.metadata.sorted_tables:
            for fk in table.foreign_keys:
                fk_targets.add(fk.column.table.name)
        # parent_chunks -> documents, chunks -> documents + parent_chunks,
        # ingest_jobs -> documents
        assert "rag_documents" in fk_targets
        assert "rag_parent_chunks" in fk_targets
