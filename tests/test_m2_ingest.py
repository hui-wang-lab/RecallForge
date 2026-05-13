"""M2 ChunkFlow ingest tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from recallforge.chunking.core.pipeline import PipelineConfig, parse_to_chunk_package
from recallforge.chunking.ir.models import Block, ChildChunk, ChunkPackage, ParentChunk, ParseReport
from recallforge.config import Settings
from recallforge.ingest.chunk_adapter import IngestContext, build_chunks_for_ingest
from recallforge.ingest.errors import ChunkKeyConflictError, IngestError, OversizeError, ParserUnavailableError
from recallforge.ingest.hashing import compute_content_hash, compute_package_content_hash
from recallforge.ingest.ingest_service import (
    SUPPORTED_DOC_TYPES_BY_SUFFIX,
    EmbeddingProviderConfig,
    IngestRequest,
    IngestService,
    _normalize_warning,
    embedding_provider_from_settings,
)
from recallforge.ingest.pipeline_config import build_pipeline_config


def _package() -> ChunkPackage:
    parent = ParentChunk(
        parent_id="parent-1",
        document_id="doc-1",
        section_id="section-1",
        heading_path=["Guide"],
        title="Guide",
        text="Guide\n\nImportant parent context.",
        page_span=(1, 1),
        source_block_ids=["b1"],
        child_chunk_ids=["child-1"],
    )
    child = ChildChunk(
        chunk_id="child-1",
        parent_id="parent-1",
        document_id="doc-1",
        chunk_type="text",
        template="generic_structured",
        text="Guide\n\nImportant child evidence.",
        page_span=(1, 1),
        source_block_ids=["b1"],
        heading_path=["Guide"],
        token_count=6,
        metadata={"block_types": ["paragraph"]},
    )
    return ChunkPackage(
        document_id="doc-1",
        document_type="generic_structured",
        parser_used="text_file",
        chunker_used="generic_structured",
        parent_chunks=[parent],
        child_chunks=[child],
        parse_report=ParseReport(
            page_count=1,
            block_count=1,
            parser_used="text_file",
            parser_fallback_chain=["text_file"],
            parent_chunk_count=1,
            child_chunk_count=1,
        ),
    )


# ── pipeline_config tests ──────────────────────────────────────


def test_pipeline_config_uses_settings_values():
    settings = Settings(
        child_max_tokens=321,
        child_min_tokens=45,
        parent_granularity="section",
        openai_api_key="test",
    )

    config = build_pipeline_config(settings, parser_hint="text_file", template_hint="manual")

    assert config.parser == "text_file"
    assert config.template == "manual"
    assert config.child_max_tokens == 321
    assert config.child_min_tokens == 45
    assert config.parent_granularity == "section"
    assert config.include_blocks is True


# ── embedding provider tests ───────────────────────────────────


def test_embedding_provider_config_comes_from_settings():
    settings = Settings(
        embedding_provider="provider-x",
        embedding_model="model-y",
        embedding_dim=768,
        openai_api_key="test",
    )

    config = embedding_provider_from_settings(settings)

    assert config.provider == "provider-x"
    assert config.model_slug == "model-y"
    assert config.dim == 768


# ── hashing tests ──────────────────────────────────────────────


def test_content_hash_normalizes_line_endings_and_trailing_spaces():
    left = compute_content_hash("﻿Title\r\nbody   \r\n\r\n\r\nend")
    right = compute_content_hash("Title\nbody\n\nend")

    assert left == right
    assert len(left) == 64
    assert left == left.lower()


def test_compute_package_content_hash_raises_on_empty_package():
    empty_package = ChunkPackage(
        document_id="doc-empty",
        document_type="generic_structured",
        parser_used="text_file",
        chunker_used="generic_structured",
    )
    with pytest.raises(IngestError, match="no parseable content"):
        compute_package_content_hash(empty_package)


def test_hash_uses_text_not_markdown_for_stability():
    """Hash must be stable across different parsers that produce different markdown."""
    blocks_a = [
        Block(block_id="b1", document_id="d1", page_number=1, block_type="paragraph",
              text="Hello world", markdown="**Hello** world"),
    ]
    blocks_b = [
        Block(block_id="b1", document_id="d1", page_number=1, block_type="paragraph",
              text="Hello world", markdown="Hello _world_"),
    ]
    pkg_a = ChunkPackage(
        document_id="d1", document_type="generic_structured",
        parser_used="docling", chunker_used="generic_structured",
        blocks=blocks_a, parent_chunks=[ParentChunk(
            parent_id="p1", document_id="d1", section_id="s1",
            heading_path=["H"], title="H", text="Hello world",
            page_span=(1, 1), source_block_ids=["b1"],
        )],
    )
    pkg_b = ChunkPackage(
        document_id="d1", document_type="generic_structured",
        parser_used="pypdf", chunker_used="generic_structured",
        blocks=blocks_b, parent_chunks=[ParentChunk(
            parent_id="p1", document_id="d1", section_id="s1",
            heading_path=["H"], title="H", text="Hello world",
            page_span=(1, 1), source_block_ids=["b1"],
        )],
    )
    assert compute_package_content_hash(pkg_a) == compute_package_content_hash(pkg_b)


# ── chunk_adapter tests ────────────────────────────────────────


def test_chunk_adapter_maps_package_with_injected_embedding_config():
    ctx = IngestContext(
        tenant_id="tenant-a",
        user_id="user-a",
        source_uri="file:///guide.md",
        source_name="guide.md",
        doc_type="markdown",
        department="eng",
        access_level="internal",
        document_version=3,
        embedding_provider="provider-x",
        embedding_model="model-y",
        embedding_dim=768,
    )

    ingest_chunks = build_chunks_for_ingest(_package(), ctx)

    assert len(ingest_chunks.parent_creates) == 1
    parent = ingest_chunks.parent_creates[0]
    assert parent.parent_key == "parent-1"
    assert parent.version == 3

    draft = ingest_chunks.child_drafts_by_parent_key["parent-1"][0]
    create = draft.to_create(parent_id=42)
    assert create.tenant_id == "tenant-a"
    assert create.parent_id == 42
    assert create.embedding_provider == "provider-x"
    assert create.embedding_model == "model-y"
    assert create.embedding_dim == 768
    assert create.chunk_type == "child"
    assert create.metadata["chunkflow_chunk_type"] == "text"
    assert "user_id" not in create.metadata


def test_chunk_adapter_rejects_duplicate_child_keys_with_chunk_key_conflict_error():
    package = _package()
    package.child_chunks.append(package.child_chunks[0])
    ctx = IngestContext(
        tenant_id="tenant-a",
        user_id=None,
        source_uri="file:///guide.md",
        source_name="guide.md",
        doc_type="markdown",
        department="eng",
        access_level="internal",
        document_version=1,
        embedding_provider="provider-x",
        embedding_model="model-y",
        embedding_dim=768,
    )

    with pytest.raises(ChunkKeyConflictError) as exc_info:
        build_chunks_for_ingest(package, ctx)

    err = exc_info.value
    assert "child-1" in err.duplicate_child_keys
    assert err.diagnostics()["chunk_key_conflicts"]["duplicate_child_keys"] == ["child-1"]


# ── ParseReport field tests ────────────────────────────────────


def test_parse_report_contains_parser_fallback_chain():
    report = ParseReport(
        page_count=5,
        block_count=10,
        parser_used="pypdf",
        parser_fallback_chain=["docling", "mineru", "pypdf"],
        parent_chunk_count=3,
        child_chunk_count=12,
    )
    d = report.to_dict()
    assert d["parser_used"] == "pypdf"
    assert d["parser_fallback_chain"] == ["docling", "mineru", "pypdf"]
    assert d["parent_chunk_count"] == 3
    assert d["child_chunk_count"] == 12


def test_pipeline_populates_parse_report_fields(tmp_path):
    source = tmp_path / "guide.md"
    source.write_text("# Guide\n\nContent here.", encoding="utf-8")

    package = parse_to_chunk_package(source, PipelineConfig(parser="text_file", template="auto"))

    assert package.parse_report.parser_used == "text_file"
    assert "text_file" in package.parse_report.parser_fallback_chain
    assert package.parse_report.parent_chunk_count >= 1
    assert package.parse_report.child_chunk_count >= 1


# ── OversizeError tests ────────────────────────────────────────


def test_oversize_error_is_ingest_error():
    err = OversizeError(
        message="too big",
        limit_name="ingest_max_file_bytes",
        actual=200,
        limit=100,
    )
    assert isinstance(err, IngestError)
    assert err.limit_name == "ingest_max_file_bytes"
    assert err.actual == 200
    assert err.limit == 100
    diag = err.diagnostics()
    assert diag["limit_breached"] == "ingest_max_file_bytes"
    assert str(err) == "too big"


# ── warning normalization tests ────────────────────────────────


def test_normalize_warning_converts_string_to_dict():
    result = _normalize_warning("something went wrong")
    assert result == {"level": "warning", "message": "something went wrong", "source": "chunkflow"}


def test_normalize_warning_passes_through_dict():
    original = {"level": "error", "message": "fail", "source": "parser"}
    assert _normalize_warning(original) is original


# ── doc_type mapping tests ─────────────────────────────────────


def test_doc_type_suffix_mapping_matches_m1_spec():
    assert SUPPORTED_DOC_TYPES_BY_SUFFIX[".txt"] == "txt"
    assert SUPPORTED_DOC_TYPES_BY_SUFFIX[".csv"] == "csv"
    assert SUPPORTED_DOC_TYPES_BY_SUFFIX[".tsv"] == "tsv"
    assert SUPPORTED_DOC_TYPES_BY_SUFFIX[".md"] == "markdown"
    assert SUPPORTED_DOC_TYPES_BY_SUFFIX[".pdf"] == "pdf"


# ── ingest_service integration tests ───────────────────────────


def _make_fake_session():
    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def begin(self):
            return self

        async def execute(self, *args, **kwargs):
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    return FakeSession()


class _FakeRepos:
    """Helper to wire up fake repositories for ingest_service tests."""

    def __init__(self, monkeypatch, *, duplicate_version=None, mark_running_called=False):
        self.state = SimpleNamespace(
            mark_running_called=mark_running_called,
            parent_bulk_called=False,
            child_bulk_called=False,
            skipped_result=None,
            failed_called=False,
        )
        self._duplicate_version = duplicate_version

        class FakeIngestJobRepository:
            def __init__(inner_self, session):
                pass

            async def create(inner_self, input):
                return SimpleNamespace(job_id=uuid.uuid4(), status="pending")

            async def mark_running(inner_self, job_id, tenant_id):
                self.state.mark_running_called = True
                return SimpleNamespace(job_id=job_id, status="running")

            async def mark_skipped_duplicate(inner_self, job_id, tenant_id, result):
                self.state.skipped_result = result
                return SimpleNamespace(job_id=job_id, status="skipped_duplicate")

            async def mark_failed(inner_self, job_id, tenant_id, error_message, diagnostics, *, warnings=None, parse_report=None):
                self.state.failed_called = True
                return SimpleNamespace(job_id=job_id, status="failed")

            async def mark_success(inner_self, job_id, tenant_id, result):
                return SimpleNamespace(job_id=job_id, status="success")

        class FakeDocumentRepository:
            def __init__(inner_self, session):
                pass

            async def find_by_source_hash(inner_self, *args, **kwargs):
                if self._duplicate_version is not None:
                    return SimpleNamespace(id=42, version=self._duplicate_version)
                return None

            async def lock_active_by_source(inner_self, *args, **kwargs):
                return None

            async def next_version(inner_self, *args, **kwargs):
                return 1

            async def create(inner_self, input):
                return SimpleNamespace(id=1, version=input.version)

        class FakeDocumentVersionRepository:
            def __init__(inner_self, session):
                pass

            async def supersede_source(inner_self, *args, **kwargs):
                return SimpleNamespace(document_count=0, parent_chunk_count=0, child_chunk_count=0)

        class FakeParentChunkRepository:
            def __init__(inner_self, session):
                pass

            async def bulk_create(inner_self, document_id, chunks):
                self.state.parent_bulk_called = True
                return [SimpleNamespace(id=i + 1, parent_key=c.parent_key) for i, c in enumerate(chunks)]

        class FakeChunkRepository:
            def __init__(inner_self, session):
                pass

            async def bulk_create(inner_self, *args, **kwargs):
                self.state.child_bulk_called = True
                return []

        monkeypatch.setattr("recallforge.ingest.ingest_service.IngestJobRepository", FakeIngestJobRepository)
        monkeypatch.setattr("recallforge.ingest.ingest_service.DocumentRepository", FakeDocumentRepository)
        monkeypatch.setattr("recallforge.ingest.ingest_service.DocumentVersionRepository", FakeDocumentVersionRepository)
        monkeypatch.setattr("recallforge.ingest.ingest_service.ParentChunkRepository", FakeParentChunkRepository)
        monkeypatch.setattr("recallforge.ingest.ingest_service.ChunkRepository", FakeChunkRepository)


@pytest.mark.asyncio
async def test_ingest_service_calls_mark_running_before_persist(tmp_path, monkeypatch):
    """P0 regression test: mark_running must be called so terminal-state methods find the job."""
    source = tmp_path / "guide.md"
    source.write_text("# Guide\n\nContent.", encoding="utf-8")
    repos = _FakeRepos(monkeypatch)

    service = IngestService(
        session_factory=lambda: _make_fake_session(),
        settings=Settings(openai_api_key="test"),
        embedding_provider=EmbeddingProviderConfig("provider-x", "model-y", 768),
        parse_function=lambda path, config: _package(),
    )

    await service.ingest_document(
        IngestRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            source_uri="file:///guide.md",
            department="eng",
            access_level="internal",
            file_path=source,
        )
    )

    assert repos.state.mark_running_called is True


@pytest.mark.asyncio
async def test_ingest_service_skips_duplicate_without_writing_chunks(tmp_path, monkeypatch):
    source = tmp_path / "guide.md"
    source.write_text("# Guide\n\nDuplicate content.", encoding="utf-8")
    repos = _FakeRepos(monkeypatch, duplicate_version=2)

    service = IngestService(
        session_factory=lambda: _make_fake_session(),
        settings=Settings(openai_api_key="test"),
        embedding_provider=EmbeddingProviderConfig("provider-x", "model-y", 768),
        parse_function=lambda path, config: _package(),
    )

    record = await service.ingest_document(
        IngestRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            source_uri="file:///guide.md",
            department="eng",
            access_level="internal",
            file_path=source,
        )
    )

    assert record.status == "skipped_duplicate"
    assert repos.state.skipped_result.document_id == 42
    assert repos.state.skipped_result.version == 2
    assert repos.state.skipped_result.metadata_patch == {
        "dedupe_existing_version": 2,
        "skipped_reason": "content_hash_match",
    }
    assert repos.state.parent_bulk_called is False
    assert repos.state.child_bulk_called is False
    for w in repos.state.skipped_result.warnings:
        assert isinstance(w, dict), f"Expected dict, got {type(w)}: {w}"
        assert "level" in w
        assert "message" in w


@pytest.mark.asyncio
async def test_ingest_service_re_raises_oversize_error_before_job_creation(tmp_path, monkeypatch):
    """Cheap-fail check (file size) now runs BEFORE _create_job."""
    source = tmp_path / "huge.pdf"
    source.write_text("x" * 100, encoding="utf-8")

    repos = _FakeRepos(monkeypatch)

    service = IngestService(
        session_factory=lambda: _make_fake_session(),
        settings=Settings(ingest_max_file_bytes=1, openai_api_key="test"),
        embedding_provider=EmbeddingProviderConfig("provider-x", "model-y", 768),
    )

    with pytest.raises(OversizeError):
        await service.ingest_document(
            IngestRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                source_uri="file:///huge.pdf",
                department="eng",
                access_level="internal",
                file_path=source,
            )
        )
    # Job was never created because oversize check comes first
    assert repos.state.mark_running_called is False


@pytest.mark.asyncio
async def test_mark_failed_does_not_swallow_original_exception(tmp_path, monkeypatch):
    """If mark_failed itself throws, the original exception must still be re-raised."""
    source = tmp_path / "guide.md"
    source.write_text("# Guide\n\nContent.", encoding="utf-8")

    class BrokenJobRepo:
        def __init__(self, session):
            pass

        async def create(self, input):
            return SimpleNamespace(job_id=uuid.uuid4(), status="pending")

        async def mark_running(self, job_id, tenant_id):
            return SimpleNamespace(job_id=job_id, status="running")

        async def mark_failed(self, *args, **kwargs):
            raise RuntimeError("DB is down")

    monkeypatch.setattr("recallforge.ingest.ingest_service.IngestJobRepository", BrokenJobRepo)

    class BrokenDocRepo:
        def __init__(self, session):
            pass

        async def find_by_source_hash(self, *args, **kwargs):
            raise RuntimeError("DB is down")

        async def lock_active_by_source(self, *args, **kwargs):
            return None

        async def next_version(self, *args, **kwargs):
            return 1

        async def create(self, input):
            return SimpleNamespace(id=1, version=1)

    monkeypatch.setattr("recallforge.ingest.ingest_service.DocumentRepository", BrokenDocRepo)

    class FakeDocVersionRepo:
        def __init__(self, session):
            pass

        async def supersede_source(self, *args, **kwargs):
            return SimpleNamespace(document_count=0, parent_chunk_count=0, child_chunk_count=0)

    monkeypatch.setattr("recallforge.ingest.ingest_service.DocumentVersionRepository", FakeDocVersionRepo)

    class FakeParentRepo:
        def __init__(self, session):
            pass

        async def bulk_create(self, document_id, chunks):
            return [SimpleNamespace(id=i + 1, parent_key=c.parent_key) for i, c in enumerate(chunks)]

    monkeypatch.setattr("recallforge.ingest.ingest_service.ParentChunkRepository", FakeParentRepo)

    class FakeChunkRepo:
        def __init__(self, session):
            pass

        async def bulk_create(self, *args, **kwargs):
            return []

    monkeypatch.setattr("recallforge.ingest.ingest_service.ChunkRepository", FakeChunkRepo)

    service = IngestService(
        session_factory=lambda: _make_fake_session(),
        settings=Settings(openai_api_key="test"),
        embedding_provider=EmbeddingProviderConfig("provider-x", "model-y", 768),
        parse_function=lambda path, config: _package(),
    )

    # The original exception (RuntimeError from find_by_source_hash) should propagate,
    # NOT the RuntimeError from mark_failed
    with pytest.raises(RuntimeError, match="DB is down"):
        await service.ingest_document(
            IngestRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                source_uri="file:///guide.md",
                department="eng",
                access_level="internal",
                file_path=source,
            )
        )


@pytest.mark.asyncio
async def test_parse_runtime_error_wrapped_as_parser_unavailable(tmp_path, monkeypatch):
    """ChunkFlow 'No parser could parse' RuntimeError should become ParserUnavailableError."""
    source = tmp_path / "guide.md"
    source.write_text("# Guide\n\nContent.", encoding="utf-8")

    repos = _FakeRepos(monkeypatch)

    service = IngestService(
        session_factory=lambda: _make_fake_session(),
        settings=Settings(openai_api_key="test"),
        embedding_provider=EmbeddingProviderConfig("provider-x", "model-y", 768),
        parse_function=lambda path, config: (_ for _ in ()).throw(
            RuntimeError("No parser could parse /some/file.pdf. Attempts: docling, pypdf")
        ),
    )

    with pytest.raises(ParserUnavailableError):
        await service.ingest_document(
            IngestRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                source_uri="file:///guide.md",
                department="eng",
                access_level="internal",
                file_path=source,
            )
        )
    assert repos.state.failed_called is True


# ── end-to-end pipeline tests ─────────────────────────────────


def test_text_pipeline_generates_parent_and_child_chunks(tmp_path):
    source = tmp_path / "guide.md"
    source.write_text("# Guide\n\nThis is the first paragraph.\n\nThis is the second paragraph.", encoding="utf-8")

    package = parse_to_chunk_package(source, PipelineConfig(parser="text_file", template="auto"))

    assert package.parser_used == "text_file"
    assert package.parent_chunks
    assert package.child_chunks
    assert package.child_chunks[0].parent_id in {parent.parent_id for parent in package.parent_chunks}


def test_csv_pipeline_generates_table_chunks(tmp_path):
    source = tmp_path / "data.csv"
    source.write_text("name,value\nalpha,1\nbeta,2\n", encoding="utf-8")

    package = parse_to_chunk_package(source, PipelineConfig(parser="table_file", template="auto"))

    assert package.parser_used == "table_file"
    assert package.document_type == "table_data"
    assert package.parent_chunks
    assert package.child_chunks
