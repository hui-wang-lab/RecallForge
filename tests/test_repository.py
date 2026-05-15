"""Repository layer unit tests using mock AsyncSession.

These tests verify repository contracts and state machine transitions
without requiring a live Postgres connection.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from recallforge.storage.models import RagChunk, RagDocument, RagIngestJob, RagParentChunk, RagQueryLog
from recallforge.storage.repository import (
    ChildChunkCreate,
    ChunkFilters,
    ChunkRepository,
    DocumentCreate,
    DocumentRepository,
    IngestJobCreate,
    IngestJobRepository,
    IngestJobSkippedDuplicate,
    IngestJobSuccess,
    QueryLogCreate,
)


def _make_mock_session() -> AsyncMock:
    return AsyncMock()


def _make_document_row(**overrides) -> RagDocument:
    defaults = dict(
        id=1,
        tenant_id="t1",
        knowledge_base_id=10,
        source_uri="file:///test.md",
        source_name="test.md",
        doc_type="markdown",
        title="Test",
        content_hash="a" * 64,
        version=1,
        status="active",
        department="eng",
        access_level="public",
        metadata_={},
        created_by=None,
        updated_by=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        deleted_at=None,
    )
    defaults.update(overrides)
    row = MagicMock(spec=RagDocument)
    for k, v in defaults.items():
        setattr(row, k, v)
    return row


def _make_parent_chunk_row(**overrides) -> RagParentChunk:
    defaults = dict(
        id=10,
        tenant_id="t1",
        knowledge_base_id=10,
        document_id=1,
        source_uri="file:///test.md",
        doc_type="markdown",
        parent_key="p1",
        chunk_index=0,
        content="parent content",
        content_hash="b" * 64,
        department="eng",
        access_level="public",
        heading_path=None,
        page_start=None,
        page_end=None,
        token_count=100,
        status="active",
        version=1,
        metadata_={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        deleted_at=None,
    )
    defaults.update(overrides)
    row = MagicMock(spec=RagParentChunk)
    for k, v in defaults.items():
        setattr(row, k, v)
    return row


def _make_chunk_row(**overrides) -> RagChunk:
    defaults = dict(
        id=100,
        tenant_id="t1",
        knowledge_base_id=10,
        document_id=1,
        parent_id=10,
        chunk_key="c1",
        parent_key="p1",
        chunk_index=0,
        content="child content",
        content_hash="c" * 64,
        doc_type="markdown",
        chunk_type="child",
        template=None,
        department="eng",
        access_level="public",
        heading_path=None,
        page_start=None,
        page_end=None,
        source_uri="file:///test.md",
        version=1,
        status="active",
        embedding_provider="dashscope",
        embedding_model="text-embedding-v4@1024",
        embedding_dim=1024,
        embedding_text_embedding_v4_1024=None,
        embedding_metadata={},
        metadata_={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        deleted_at=None,
    )
    defaults.update(overrides)
    row = MagicMock(spec=RagChunk)
    for k, v in defaults.items():
        setattr(row, k, v)
    return row


def _make_job_row(**overrides) -> RagIngestJob:
    job_id = uuid.uuid4()
    defaults = dict(
        id=1,
        job_id=job_id,
        tenant_id="t1",
        knowledge_base_id=10,
        document_id=None,
        source_uri="file:///test.md",
        source_name="test.md",
        doc_type="markdown",
        status="pending",
        content_hash=None,
        version=None,
        parser=None,
        template=None,
        parser_used=None,
        chunker_used=None,
        parent_chunk_count=0,
        child_chunk_count=0,
        warnings=[],
        parse_report={},
        error_message=None,
        metadata_={},
        created_by=None,
        started_at=None,
        finished_at=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    row = MagicMock(spec=RagIngestJob)
    for k, v in defaults.items():
        setattr(row, k, v)
    return row


# ── DocumentRepository tests ──────────────────────────────────────


class TestDocumentRepositoryCreate:
    @pytest.mark.asyncio
    async def test_create_sets_status_active(self):
        session = _make_mock_session()
        repo = DocumentRepository(session)
        input_ = DocumentCreate(
            tenant_id="t1",
            source_uri="file:///x.md",
            doc_type="markdown",
            content_hash="a" * 64,
            department="eng",
            access_level="public",
        )
        record = await repo.create(input_)
        assert record.status == "active"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_maps_metadata_underscore(self):
        session = _make_mock_session()
        repo = DocumentRepository(session)
        input_ = DocumentCreate(
            tenant_id="t1",
            source_uri="file:///x.md",
            doc_type="markdown",
            content_hash="a" * 64,
            department="eng",
            access_level="public",
            metadata={"key": "val"},
        )
        _record = await repo.create(input_)
        # The ORM row passed to session.add should have metadata_=...
        added_row = session.add.call_args[0][0]
        assert added_row.metadata_ == {"key": "val"}


class TestDocumentRepositoryMarkDeleted:
    @pytest.mark.asyncio
    async def test_mark_deleted_cascades_to_chunks(self):
        """mark_deleted must cascade to rag_parent_chunks and rag_chunks."""
        session = _make_mock_session()
        repo = DocumentRepository(session)

        # Mock the update result: document was updated
        update_result = MagicMock()
        update_result.rowcount = 1
        session.execute.return_value = update_result

        # Mock session.get for the final read
        doc_row = _make_document_row(status="deleted")
        session.get = AsyncMock(return_value=doc_row)

        _record = await repo.mark_deleted(1, "t1")

        # session.execute should be called 3 times: doc update, parent update, child update
        assert session.execute.await_count == 3

        # Verify the second call is parent chunk cascade
        parent_call_args = session.execute.call_args_list[1]
        parent_stmt = parent_call_args[0][0]
        # The statement should be an update on RagParentChunk
        assert "rag_parent_chunks" in str(parent_stmt)

        # Verify the third call is child chunk cascade
        child_call_args = session.execute.call_args_list[2]
        child_stmt = child_call_args[0][0]
        assert "rag_chunks" in str(child_stmt)


class TestDocumentRepositoryNextVersion:
    @pytest.mark.asyncio
    async def test_next_version_returns_max_plus_one(self):
        session = _make_mock_session()
        repo = DocumentRepository(session)
        scalar_result = MagicMock()
        scalar_result.scalar.return_value = 3
        session.execute.return_value = scalar_result

        version = await repo.next_version("t1", "file:///x.md")
        assert version == 4

    @pytest.mark.asyncio
    async def test_next_version_returns_1_when_no_documents(self):
        session = _make_mock_session()
        repo = DocumentRepository(session)
        scalar_result = MagicMock()
        scalar_result.scalar.return_value = None
        session.execute.return_value = scalar_result

        version = await repo.next_version("t1", "file:///x.md")
        assert version == 1


# ── IngestJobRepository tests ─────────────────────────────────────


class TestIngestJobStatusMachine:
    @pytest.mark.asyncio
    async def test_create_sets_pending_status(self):
        session = _make_mock_session()
        repo = IngestJobRepository(session)
        input_ = IngestJobCreate(tenant_id="t1", source_uri="file:///x.md")

        record = await repo.create(input_)
        assert record.status == "pending"
        # Verify job_id is a UUID
        assert isinstance(record.job_id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_mark_running_from_pending(self):
        session = _make_mock_session()
        repo = IngestJobRepository(session)

        job_id = uuid.uuid4()
        update_result = MagicMock()
        update_result.rowcount = 1
        session.execute.return_value = update_result

        # Mock the re-select after update
        job_row = _make_job_row(job_id=job_id, status="running")
        scalar_result = MagicMock()
        scalar_result.scalar_one.return_value = job_row
        # First call is update, second is select
        session.execute.side_effect = [update_result, scalar_result]

        record = await repo.mark_running(job_id, "t1")
        assert record.status == "running"

    @pytest.mark.asyncio
    async def test_mark_success_from_running(self):
        session = _make_mock_session()
        repo = IngestJobRepository(session)

        job_id = uuid.uuid4()
        update_result = MagicMock()
        update_result.rowcount = 1
        job_row = _make_job_row(job_id=job_id, status="success", document_id=42)
        scalar_result = MagicMock()
        scalar_result.scalar_one.return_value = job_row
        session.execute.side_effect = [update_result, scalar_result]

        result = IngestJobSuccess(
            document_id=42,
            content_hash="a" * 64,
            version=1,
            parent_chunk_count=2,
            child_chunk_count=5,
        )
        record = await repo.mark_success(job_id, "t1", result)
        assert record.status == "success"

    @pytest.mark.asyncio
    async def test_mark_failed_from_running(self):
        session = _make_mock_session()
        repo = IngestJobRepository(session)

        job_id = uuid.uuid4()
        # First call: select existing metadata
        meta_result = MagicMock()
        meta_result.scalar_one_or_none.return_value = {"key": "old"}
        # Second call: update
        update_result = MagicMock()
        update_result.rowcount = 1
        # Third call: re-select
        job_row = _make_job_row(job_id=job_id, status="failed")
        scalar_result = MagicMock()
        scalar_result.scalar_one.return_value = job_row

        session.execute.side_effect = [meta_result, update_result, scalar_result]

        record = await repo.mark_failed(
            job_id,
            "t1",
            "parse error",
            {"detail": "stack"},
            warnings=[{"level": "error", "message": "parse error"}],
            parse_report={"error_phase": "parse"},
        )
        assert record.status == "failed"

    @pytest.mark.asyncio
    async def test_mark_success_rejects_non_running(self):
        session = _make_mock_session()
        repo = IngestJobRepository(session)

        job_id = uuid.uuid4()
        update_result = MagicMock()
        update_result.rowcount = 0  # No rows matched (not in running state)
        session.execute.return_value = update_result

        result = IngestJobSuccess(document_id=42, content_hash="a" * 64, version=1)
        with pytest.raises(ValueError, match="not in running state"):
            await repo.mark_success(job_id, "t1", result)

    @pytest.mark.asyncio
    async def test_mark_skipped_duplicate(self):
        session = _make_mock_session()
        repo = IngestJobRepository(session)

        job_id = uuid.uuid4()
        update_result = MagicMock()
        update_result.rowcount = 1
        job_row = _make_job_row(job_id=job_id, status="skipped_duplicate")
        meta_result = MagicMock()
        meta_result.scalar_one_or_none.return_value = {"key": "old"}
        scalar_result = MagicMock()
        scalar_result.scalar_one.return_value = job_row
        session.execute.side_effect = [meta_result, update_result, scalar_result]

        result = IngestJobSkippedDuplicate(
            document_id=42,
            content_hash="a" * 64,
            version=1,
            parser_used="pypdf",
            chunker_used="generic_structured",
            parent_chunk_count=2,
            child_chunk_count=5,
            warnings=[{"level": "info"}],
            parse_report={"parser_used": "pypdf"},
            metadata_patch={"skipped_reason": "content_hash_match"},
        )
        record = await repo.mark_skipped_duplicate(job_id, "t1", result)
        assert record.status == "skipped_duplicate"


# ── QueryLogRepository tests ──────────────────────────────────────


class TestQueryLogCreateDefaults:
    def test_default_status_is_failed(self):
        """QueryLogCreate must default to 'failed' so incomplete logs are safe."""
        create = QueryLogCreate(
            request_id=uuid.uuid4(),
            tenant_id="t1",
            user_id="u1",
            department="eng",
            access_level="public",
            question="what?",
            search_mode="vector",
        )
        assert create.status == "failed"


# ── Data record mapping tests ─────────────────────────────────────


class TestRecordMapping:
    def test_doc_record_maps_metadata_underscore(self):
        from recallforge.storage.repository import _doc_to_record

        row = _make_document_row(metadata_={"foo": "bar"})
        record = _doc_to_record(row)
        assert record.metadata == {"foo": "bar"}

    def test_chunk_record_maps_metadata_underscore(self):
        from recallforge.storage.repository import _chunk_to_record

        row = _make_chunk_row(metadata_={"block": True}, embedding_metadata={"col": {}})
        record = _chunk_to_record(row)
        assert record.metadata == {"block": True}
        assert record.embedding_metadata == {"col": {}}

    def test_job_record_maps_metadata_underscore(self):
        from recallforge.storage.repository import _job_to_record

        row = _make_job_row(metadata_={"diag": True})
        record = _job_to_record(row)
        assert record.metadata == {"diag": True}

    def test_query_log_record_numeric_cast(self):
        from recallforge.storage.repository import _query_log_to_record

        row = MagicMock(spec=RagQueryLog)
        row.id = 1
        row.request_id = uuid.uuid4()
        row.tenant_id = "t1"
        row.user_id = "u1"
        row.department = "eng"
        row.access_level = "public"
        row.question = "q"
        row.rewritten_query = None
        row.filters = {}
        row.client_filters = {}
        row.search_mode = "vector"
        row.embedding_provider = "dashscope"
        row.embedding_model = "text-embedding-v4@1024"
        row.embedding_dim = 1024
        row.reranker_provider = None
        row.reranker_model = None
        row.top_k = 30
        row.final_top_k = 8
        from decimal import Decimal
        row.min_rerank_score = Decimal("0.3500")
        row.min_top1_margin = Decimal("0.0500")
        row.max_context_tokens = 24000
        row.hit_summary = []
        row.selected_references = []
        row.answer = "a"
        row.refusal_reason = None
        row.latencies_ms = {}
        row.metadata_ = {}
        row.status = "success"
        row.error_message = None
        row.created_at = datetime.now(UTC)

        record = _query_log_to_record(row)
        assert isinstance(record.min_rerank_score, float)
        assert isinstance(record.min_top1_margin, float)
        assert abs(record.min_rerank_score - 0.35) < 1e-6


# ── Timestamp timezone tests ──────────────────────────────────────


class TestTimestampTimezone:
    """Verify that all repository timestamps use UTC-aware datetime."""

    @pytest.mark.asyncio
    async def test_mark_deleted_uses_utc(self):
        session = _make_mock_session()
        repo = DocumentRepository(session)
        update_result = MagicMock()
        update_result.rowcount = 1
        session.execute.return_value = update_result
        doc_row = _make_document_row(status="deleted")
        session.get = AsyncMock(return_value=doc_row)

        with patch("recallforge.storage.repository.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, tzinfo=UTC)
            await repo.mark_deleted(1, "t1")

            mock_dt.now.assert_called_with(UTC)


# ── ChunkFilters defaults ─────────────────────────────────────────


class TestChunkFilters:
    def test_default_status_is_active(self):
        f = ChunkFilters(tenant_id="t1")
        assert f.status == "active"

    def test_optional_fields_default_none(self):
        f = ChunkFilters(tenant_id="t1")
        assert f.department is None
        assert f.access_level is None
        assert f.doc_type is None
        assert f.version is None
        assert f.source_uri is None


class TestChunkRepositoryEmbeddingBackfill:
    @pytest.mark.asyncio
    async def test_list_for_embedding_backfill_returns_vector_metadata_source_fields(self):
        session = _make_mock_session()
        row = _make_chunk_row(
            chunk_key="child-1",
            parent_key="parent-1",
            template="generic_structured",
            heading_path=["Guide"],
            page_start=1,
            page_end=2,
        )
        result = MagicMock()
        result.scalars.return_value.all.return_value = [row]
        session.execute.return_value = result

        records = await ChunkRepository(session).list_for_embedding_backfill(
            "text-embedding-v4@1024",
            limit=10,
            tenant_id="t1",
        )

        assert len(records) == 1
        source = records[0]
        assert source.chunk_key == "child-1"
        assert source.parent_key == "parent-1"
        assert source.doc_type == "markdown"
        assert source.template == "generic_structured"
        assert source.heading_path == ["Guide"]
        assert source.source_uri == "file:///test.md"
        assert not hasattr(source, "embedding_model")


# ── ChildChunkCreate embedding config ──────────────────────────────


class TestChildChunkCreateEmbeddingConfig:
    def test_embedding_config_is_explicit(self):
        c = ChildChunkCreate(
            tenant_id="t1",
            parent_id=1,
            parent_key="p1",
            chunk_key="c1",
            chunk_index=0,
            content="hello",
            content_hash="a" * 64,
            doc_type="markdown",
            department="eng",
            access_level="public",
            source_uri="file:///x.md",
            embedding_provider="dashscope",
            embedding_model="text-embedding-v4@1024",
            embedding_dim=1024,
        )
        assert c.embedding_provider == "dashscope"
        assert c.embedding_model == "text-embedding-v4@1024"
        assert c.embedding_dim == 1024
