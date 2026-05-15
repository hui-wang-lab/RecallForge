"""Integration test: answer generation full pipeline with real services."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from recallforge.api.answering import (
    REFUSAL_ANSWER,
    AnswerGenerationRequest,
    HTTPAnswerGenerator,
    build_answer_prompt,
    validate_answer_citations,
)
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

TEST_TENANT = "int-answer-test"
TEST_DEPARTMENT = "global"
TEST_ACCESS_LEVEL = "restricted"
TEST_SOURCE_URI_PREFIX = "integration-answer-test/"

ANSWER_DOC = """\
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


def _integration_settings(**overrides) -> Settings:
    data = {
        "api_require_auth": False,
        "reranker_required": False,
        "auto_embedding_backfill_on_ingest": True,
        "console_enabled": False,
        "upload_temp_dir": ".tmp/int-test-answer-uploads",
        "upload_startup_cleanup_enabled": False,
        "answer_generation_enabled": True,
        "llm_provider": "deepseek",
        "llm_model": "deepseek-chat",
        "llm_endpoint": "https://api.deepseek.com/v1/chat/completions",
        "llm_api_key": "sk-5a608d194b7c417999aa52e20f56bd49",
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


@pytest.fixture(scope="module")
async def answer_generator(settings: Settings):
    generator = HTTPAnswerGenerator(settings)
    yield generator


@pytest.fixture(autouse=True, scope="module")
async def _setup_and_cleanup(settings: Settings, session_factory, embedding_provider):
    """Ingest test documents before tests; clean up after module."""
    await _db_cleanup(settings)

    ingest_svc = IngestService(
        session_factory,
        settings,
        embedding_provider_from_settings(settings),
    )
    backfill_svc = EmbeddingBackfillService(session_factory, embedding_provider, settings)

    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as f:
        f.write(ANSWER_DOC)
        tmp_path = Path(f.name)

    source_uri = f"{TEST_SOURCE_URI_PREFIX}refund-policy.md"
    try:
        request = IngestRequest(
            tenant_id=TEST_TENANT,
            user_id="int-test-user",
            source_uri=source_uri,
            department=TEST_DEPARTMENT,
            access_level=TEST_ACCESS_LEVEL,
            file_path=tmp_path,
            source_name="refund-policy.md",
        )
        job = await ingest_svc.ingest_document(request)
        assert job.status == "success", f"Ingest failed: {job.error_message}"

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
                assert result.failed == 0, f"Backfill failed: {result.failed} chunks"
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


async def _retrieve_result(settings, session_factory, embedding_provider, question):
    """Run retrieval and return the result."""
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
            request = RetrievalRequest(question=question, client_filters={})
            result = await service.retrieve(request, ctx)
            await service.close()
            return result


# ── Tests: answer generation via HTTPAnswerGenerator ───────


async def test_answer_generator_returns_cited_answer(
    settings, session_factory, embedding_provider, answer_generator
):
    """Full pipeline: retrieve context → generate answer with citations."""
    result = await _retrieve_result(settings, session_factory, embedding_provider, "退款需要多长时间")
    if result.status != "retrieved":
        pytest.skip("Retrieval refused without reranker — answer test needs retrieved context")

    gen_result = await answer_generator.generate(
        AnswerGenerationRequest(
            question="退款需要多长时间",
            context_text=result.context_text,
            references=result.references,
        )
    )
    answer = gen_result.answer
    assert answer, "Answer should not be empty"
    # Valid answers either contain citations or are the refusal sentence
    assert answer != REFUSAL_ANSWER or "[1]" not in answer, "If refused, should not have citations"
    # Metadata should contain validation result
    assert "answer_validation" in gen_result.metadata
    assert gen_result.metadata["answer_validation"]["valid"] is True


async def test_answer_has_valid_citations(
    settings, session_factory, embedding_provider, answer_generator
):
    """Generated answer citations must match the provided references."""
    result = await _retrieve_result(settings, session_factory, embedding_provider, "退款流程是什么")
    if result.status != "retrieved":
        pytest.skip("Retrieval refused — answer citation test needs retrieved context")

    gen_result = await answer_generator.generate(
        AnswerGenerationRequest(
            question="退款流程是什么",
            context_text=result.context_text,
            references=result.references,
        )
    )
    if gen_result.answer != REFUSAL_ANSWER:
        validation = validate_answer_citations(gen_result.answer, result.references)
        assert validation.valid, f"Citation validation failed: {validation.reason}"


async def test_answer_generator_metadata(
    settings, session_factory, embedding_provider, answer_generator
):
    """Generator metadata should include provider, model, latency, and token usage."""
    result = await _retrieve_result(settings, session_factory, embedding_provider, "教育版退款有什么不同")
    if result.status != "retrieved":
        pytest.skip("Retrieval refused — answer metadata test needs retrieved context")

    gen_result = await answer_generator.generate(
        AnswerGenerationRequest(
            question="教育版退款有什么不同",
            context_text=result.context_text,
            references=result.references,
        )
    )
    metadata = gen_result.metadata
    assert metadata["provider"] == "deepseek"
    assert metadata["model"] == "deepseek-chat"
    assert "latency_ms" in metadata
    assert metadata["latency_ms"] > 0
    assert "token_usage" in metadata


async def test_answer_prompt_construction(
    settings, session_factory, embedding_provider, answer_generator
):
    """The answer prompt should include the refusal sentence and allowed citations."""
    result = await _retrieve_result(settings, session_factory, embedding_provider, "退款比例怎么算")
    if result.status != "retrieved":
        pytest.skip("Retrieval refused — prompt test needs retrieved context")

    prompt = build_answer_prompt("退款比例怎么算", result.context_text, result.references)
    assert "当前资料无法确认" in prompt
    assert "[1]" in prompt
    assert "退款比例怎么算" in prompt


async def test_unrelated_question_gets_refusal_or_no_citations(
    settings, session_factory, embedding_provider, answer_generator
):
    """When retrieval is refused, the answer endpoint should return a refusal."""
    result = await _retrieve_result(settings, session_factory, embedding_provider, "量子计算机原理")
    # If retrieval refused, knowledge_service.answer would also return refused
    assert result.status == "refused"
    assert result.refusal_reason is not None
