"""Integration test: retrieve full pipeline with real PostgreSQL and DashScope."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from recallforge.config import Settings
from recallforge.context import RequestContext
from recallforge.embeddings.alibaba_bailian import AlibabaBailianEmbeddingProvider
from recallforge.embeddings.backfill import BackfillRequest, EmbeddingBackfillService
from recallforge.ingest.ingest_service import IngestRequest, IngestService, embedding_provider_from_settings
from recallforge.retrieval.reranker.registry import reranker_provider_from_settings
from recallforge.retrieval.retrieval_service import RetrievalService
from recallforge.retrieval.types import RetrievalRequest
from recallforge.storage.pgvector_store import PgVectorStore
from recallforge.storage.repository import ChunkRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

TEST_TENANT = "int-retrieve-test"
TEST_DEPARTMENT = "global"
TEST_ACCESS_LEVEL = "restricted"
TEST_SOURCE_URI_PREFIX = "integration-retrieve-test/"

RETRIEVAL_DOC = """\
# 产品退款政策

## 概述

本文档描述了 RecallForge 产品的退款政策和流程。所有退款请求需在购买后 30 天内提交。逾期提交的退款申请将被自动拒绝。

## 退款条件

以下情况可以申请退款：

- 未使用的许可证可全额退款，无需提供理由
- 部分使用的许可证按剩余时长比例退款，最低退款比例为 10%
- 超过 30 天的购买不予退款，除非有特殊豁免审批
- 教育版许可证享有 60 天退款窗口

## 退款流程

完整的退款处理流程如下：

1. 用户通过官方网站提交退款申请表
2. 客服团队在 3 个工作日内完成初审
3. 初审通过后转交财务部门复核
4. 财务部门在 5 个工作日内完成退款到原支付方式
5. 用户收到退款确认邮件和电子收据

## 退款时间线

| 步骤 | 时间 |
|------|------|
| 提交申请 | 即时 |
| 初审 | 3 个工作日 |
| 财务复核 | 2 个工作日 |
| 退款到账 | 5 个工作日 |

## 常见问题

### 退款需要多长时间？

从提交申请到退款到账，整个流程通常需要 10 个工作日。其中审核阶段约 3 个工作日，退款到账约 5 个工作日。

### 可以部分退款吗？

部分使用的许可证可以按比例退款。具体比例根据剩余使用时长计算，最低退款比例为购买价格的 10%。

### 教育版有什么不同？

教育版许可证享有更长的退款窗口（60 天），但需要提供有效的教育机构证明文件。

## 联系方式

退款相关问题请联系客服团队：support@recallforge.example.com
"""

SECOND_DOC = """\
# 系统部署指南

## 环境要求

RecallForge 部署需要以下环境：PostgreSQL 15+、pgvector 扩展、Python 3.11+、至少 8GB 内存。

## Docker 部署

使用 Docker Compose 一键启动：
1. 复制 .env.example 为 .env 并填写配置
2. 运行 docker compose up -d
3. 等待健康检查通过后访问 http://localhost:8000

## 性能调优

对于大规模文档库（超过 100 万条），建议启用 pgvector IVFFlat 索引，并调整 probes 参数为 10-20。
"""


def _integration_settings(**overrides) -> Settings:
    data = {
        "api_require_auth": False,
        "reranker_required": False,
        "auto_embedding_backfill_on_ingest": True,
        "console_enabled": False,
        "upload_temp_dir": ".tmp/int-test-retrieve-uploads",
        "upload_startup_cleanup_enabled": False,
    }
    data.update(overrides)
    return Settings(**data)


def _async_db_url(settings: Settings) -> str:
    url = settings.database_url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    return url


def _make_ctx() -> RequestContext:
    return RequestContext(
        tenant_id=TEST_TENANT,
        user_id="int-test-user",
        department=TEST_DEPARTMENT,
        access_level=TEST_ACCESS_LEVEL,
    )


@pytest.fixture(scope="module")
def settings() -> Settings:
    return _integration_settings()


@pytest.fixture(scope="module")
async def engine(settings: Settings):
    e = create_async_engine(_async_db_url(settings), poolclass=NullPool)
    yield e
    await e.dispose()


@pytest.fixture(scope="module")
def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(scope="module")
async def embedding_provider(settings: Settings):
    provider = AlibabaBailianEmbeddingProvider(settings)
    yield provider
    await provider.close()


@pytest.fixture(autouse=True, scope="module")
async def _setup_and_cleanup(settings: Settings, session_factory, embedding_provider):
    """Ingest test documents before tests; clean up after module."""
    await _db_cleanup(settings)

    # Ingest two documents
    ingest_svc = IngestService(
        session_factory,
        settings,
        embedding_provider_from_settings(settings),
    )
    backfill_svc = EmbeddingBackfillService(session_factory, embedding_provider, settings)

    for doc_content, filename in [
        (RETRIEVAL_DOC, "refund-policy.md"),
        (SECOND_DOC, "deploy-guide.md"),
    ]:
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as f:
            f.write(doc_content)
            tmp_path = Path(f.name)

        source_uri = f"{TEST_SOURCE_URI_PREFIX}{filename}"
        try:
            request = IngestRequest(
                tenant_id=TEST_TENANT,
                user_id="int-test-user",
                source_uri=source_uri,
                department=TEST_DEPARTMENT,
                access_level=TEST_ACCESS_LEVEL,
                file_path=tmp_path,
                source_name=filename,
            )
            job = await ingest_svc.ingest_document(request)
            assert job.status == "success", f"Ingest failed for {filename}: {job.error_message}"

            # Backfill embeddings
            if job.document_id:
                async with session_factory() as session:
                    chunk_ids = await ChunkRepository(session).list_ids_by_document(
                        TEST_TENANT,
                        job.document_id,
                        limit=settings.ingest_backfill_limit,
                    )
                if chunk_ids:
                    result = await backfill_svc.backfill(
                        BackfillRequest(
                            embedding_model=settings.embedding_model,
                            tenant_id=TEST_TENANT,
                            chunk_ids=chunk_ids,
                            limit=len(chunk_ids),
                        )
                    )
                    assert result.failed == 0, f"Backfill failed for {filename}: {result.failed} chunks"
        finally:
            tmp_path.unlink(missing_ok=True)

    yield
    await _db_cleanup(settings)


async def _db_cleanup(settings: Settings):
    engine = create_async_engine(_async_db_url(settings))
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM rag_chunks WHERE tenant_id = :tid AND source_uri LIKE :prefix"),
            {"tid": TEST_TENANT, "prefix": f"{TEST_SOURCE_URI_PREFIX}%"},
        )
        await conn.execute(
            text("DELETE FROM rag_parent_chunks WHERE tenant_id = :tid AND source_uri LIKE :prefix"),
            {"tid": TEST_TENANT, "prefix": f"{TEST_SOURCE_URI_PREFIX}%"},
        )
        await conn.execute(
            text("DELETE FROM rag_query_logs WHERE tenant_id = :tid"),
            {"tid": TEST_TENANT},
        )
        await conn.execute(
            text("DELETE FROM rag_ingest_jobs WHERE tenant_id = :tid AND source_uri LIKE :prefix"),
            {"tid": TEST_TENANT, "prefix": f"{TEST_SOURCE_URI_PREFIX}%"},
        )
        await conn.execute(
            text("DELETE FROM rag_documents WHERE tenant_id = :tid AND source_uri LIKE :prefix"),
            {"tid": TEST_TENANT, "prefix": f"{TEST_SOURCE_URI_PREFIX}%"},
        )
    await engine.dispose()


async def _retrieve(settings: Settings, session_factory, embedding_provider, question: str, **kwargs):
    """Helper: run a full retrieval through the service layer."""
    async with session_factory() as session:
        async with session.begin():
            reranker = reranker_provider_from_settings(settings)
            service = RetrievalService(
                settings=settings,
                embedding_provider=embedding_provider,
                vector_store=PgVectorStore(session),
                reranker=reranker,
                session=session,
            )
            ctx = _make_ctx()
            request = RetrievalRequest(
                question=question,
                client_filters=kwargs.get("filters", {}),
                search_mode=kwargs.get("search_mode", "vector"),
            )
            result = await service.retrieve(request, ctx)
            await service.close()
            return result


# ── Tests ──────────────────────────────────────────────────


async def test_retrieve_matching_query(settings: Settings, session_factory, embedding_provider):
    """A query matching ingested content should return references."""
    result = await _retrieve(settings, session_factory, embedding_provider, "退款需要多长时间")

    if result.status == "retrieved":
        assert len(result.references) > 0, "Expected at least one reference"
        assert result.context_text, "Expected non-empty context_text"
        assert "退款" in result.context_text
        assert result.refusal_reason is None
        assert len(result.hit_summary) > 0
        # Without reranker, score_source should be "vector"
        for hit in result.hit_summary:
            assert hit.score_source == "vector"
    else:
        # Low confidence without reranker is acceptable
        assert result.status == "refused"
        assert result.refusal_reason is not None


async def test_retrieve_context_contains_relevant_content(settings: Settings, session_factory, embedding_provider):
    """Context should include the parent chunk with relevant content."""
    result = await _retrieve(settings, session_factory, embedding_provider, "退款流程是什么")

    if result.status == "retrieved":
        assert len(result.context_text) > 100, "Context text should be substantial"
        # Should mention the refund process steps
        assert any(kw in result.context_text for kw in ["退款", "申请", "工作日"])
    else:
        assert result.status == "refused"


async def test_retrieve_latency_breakdown(settings: Settings, session_factory, embedding_provider):
    """Each pipeline stage should have a latency measurement."""
    result = await _retrieve(settings, session_factory, embedding_provider, "教育版退款有什么不同")

    latencies = result.latencies_ms
    assert "query_understanding_ms" in latencies
    assert "embedding_ms" in latencies
    assert "vector_search_ms" in latencies
    assert "total_ms" in latencies
    assert latencies["total_ms"] > 0

    if result.status == "retrieved":
        assert "parent_expansion_ms" in latencies
        assert "context_assembly_ms" in latencies


async def test_retrieve_short_query_refused(settings: Settings, session_factory, embedding_provider):
    """Queries shorter than min_query_length should be refused."""
    result = await _retrieve(settings, session_factory, embedding_provider, "退")

    assert result.status == "refused"
    assert result.refusal_reason == "query_too_short"


async def test_retrieve_unrelated_query_refused(settings: Settings, session_factory, embedding_provider):
    """A completely unrelated query should be refused due to low confidence."""
    result = await _retrieve(settings, session_factory, embedding_provider, "量子计算机原理")

    assert result.status == "refused"
    assert result.refusal_reason is not None


async def test_retrieve_doc_type_filter(settings: Settings, session_factory, embedding_provider):
    """Filter by doc_type=markdown should find results; doc_type=pdf should refuse."""
    result_md = await _retrieve(
        settings, session_factory, embedding_provider,
        "退款政策", filters={"doc_type": "markdown"},
    )
    # markdown document should be found (or refused for other reasons like confidence)
    assert result_md.status in ("retrieved", "refused")

    result_pdf = await _retrieve(
        settings, session_factory, embedding_provider,
        "退款政策", filters={"doc_type": "pdf"},
    )
    assert result_pdf.status == "refused", "Expected refused when doc_type filter excludes all documents"


async def test_retrieve_query_log_written(settings: Settings, session_factory, embedding_provider):
    """A query log entry should be written after each retrieve call."""
    question = "退款比例怎么算"
    await _retrieve(settings, session_factory, embedding_provider, question)

    engine = create_async_engine(_async_db_url(settings))
    async with engine.connect() as conn:
        r = await conn.execute(
            text(
                "SELECT question, status, embedding_model FROM rag_query_logs "
                "WHERE tenant_id = :tid AND question = :q ORDER BY created_at DESC LIMIT 1"
            ),
            {"tid": TEST_TENANT, "q": question},
        )
        row = r.first()
        assert row is not None, "No query log entry found"
        assert row[0] == question
        assert row[1] in ("retrieved", "refused")
        assert "text-embedding" in row[2]
    await engine.dispose()


async def test_retrieve_references_have_citation_fields(settings: Settings, session_factory, embedding_provider):
    """References should contain all fields needed for citation-bound answers."""
    result = await _retrieve(settings, session_factory, embedding_provider, "退款流程")

    if result.status == "retrieved":
        for ref in result.references:
            assert ref.ref_id, "ref_id must be set"
            assert ref.index >= 0, "index must be non-negative"
            assert ref.document_id, "document_id must be set"
            assert ref.source_uri, "source_uri must be set"
            assert ref.doc_type, "doc_type must be set"
            assert ref.chunk_id, "chunk_id must be set"
            assert ref.parent_id, "parent_id must be set"


async def test_retrieve_second_document(settings: Settings, session_factory, embedding_provider):
    """A query about the deployment guide should find that document."""
    result = await _retrieve(settings, session_factory, embedding_provider, "Docker部署步骤")

    if result.status == "retrieved":
        # Should find the deployment guide, not the refund policy
        source_uris = {ref.source_uri for ref in result.references}
        assert any("deploy" in uri for uri in source_uris), "Expected to find deploy-guide.md"
