"""Integration test: ingest full pipeline with real PostgreSQL and DashScope."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from recallforge.api.app import create_app
from recallforge.config import Settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

# When api_require_auth=False, the dev auth context uses tenant_id="dev-tenant"
TEST_TENANT = "dev-tenant"
TEST_SOURCE_URI_PREFIX = "integration-test/"

MARKDOWN_CONTENT = """\
# 产品退款政策

## 概述

本文档描述了 RecallForge 产品的退款政策和流程。所有退款请求需在购买后 30 天内提交。

## 退款条件

- 未使用的许可证可全额退款
- 部分使用的许可证按比例退款
- 超过 30 天的购买不予退款

## 退款流程

1. 用户提交退款申请
2. 客服团队在 3 个工作日内审核
3. 审核通过后 5 个工作日内退款到原支付方式
4. 用户收到退款确认邮件

## 常见问题

### 退款需要多长时间？

退款审核通常需要 3 个工作日，退款到账需要 5 个工作日。

### 可以部分退款吗？

部分使用的许可证可以按比例退款，具体比例根据使用时长计算。
"""

TXT_CONTENT = """\
RecallForge 是一个企业级知识库 RAG 系统，采用 recall-first 设计理念。
系统支持多种文档格式的智能解析和分块，包括 Markdown、PDF、CSV 等格式。
核心能力包括向量检索、重排序、上下文组装和引用约束的答案生成。
"""


def _integration_settings(**overrides) -> Settings:
    data = {
        "api_require_auth": False,
        "reranker_required": False,
        "auto_embedding_backfill_on_ingest": True,
        "console_enabled": False,
        "upload_temp_dir": ".tmp/int-test-uploads",
        "upload_startup_cleanup_enabled": False,
    }
    data.update(overrides)
    return Settings(**data)


def _async_db_url(settings: Settings) -> str:
    url = settings.database_url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    return url


@pytest.fixture(scope="module")
def settings() -> Settings:
    return _integration_settings()


@pytest.fixture(scope="module")
def app(settings: Settings):
    application = create_app(settings)
    yield application


@pytest.fixture(scope="module")
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture(autouse=True, scope="module")
async def cleanup_test_data(settings: Settings):
    """Clean up test data before and after the module."""
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
            text("DELETE FROM rag_ingest_jobs WHERE tenant_id = :tid AND source_uri LIKE :prefix"),
            {"tid": TEST_TENANT, "prefix": f"{TEST_SOURCE_URI_PREFIX}%"},
        )
        await conn.execute(
            text("DELETE FROM rag_documents WHERE tenant_id = :tid AND source_uri LIKE :prefix"),
            {"tid": TEST_TENANT, "prefix": f"{TEST_SOURCE_URI_PREFIX}%"},
        )
    await engine.dispose()
    yield
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
            text("DELETE FROM rag_ingest_jobs WHERE tenant_id = :tid AND source_uri LIKE :prefix"),
            {"tid": TEST_TENANT, "prefix": f"{TEST_SOURCE_URI_PREFIX}%"},
        )
        await conn.execute(
            text("DELETE FROM rag_documents WHERE tenant_id = :tid AND source_uri LIKE :prefix"),
            {"tid": TEST_TENANT, "prefix": f"{TEST_SOURCE_URI_PREFIX}%"},
        )
    await engine.dispose()


async def _verify_document_in_db(settings: Settings, source_uri: str, expected_child_chunks: int):
    engine = create_async_engine(_async_db_url(settings))
    async with engine.connect() as conn:
        r = await conn.execute(
            text("SELECT id, status FROM rag_documents WHERE source_uri = :uri AND tenant_id = :tid LIMIT 1"),
            {"uri": source_uri, "tid": TEST_TENANT},
        )
        doc = r.first()
        assert doc is not None, f"Document not found for source_uri={source_uri}"
        assert doc[1] == "active"
        doc_id = doc[0]

        r = await conn.execute(
            text("SELECT count(*) FROM rag_parent_chunks WHERE document_id = :did"),
            {"did": doc_id},
        )
        assert r.scalar() > 0, "No parent chunks found"

        r = await conn.execute(
            text("SELECT count(*) FROM rag_chunks WHERE document_id = :did"),
            {"did": doc_id},
        )
        child_count = r.scalar()
        assert child_count > 0, "No child chunks found"
        assert child_count == expected_child_chunks, (
            f"Child chunk count mismatch: db={child_count}, expected={expected_child_chunks}"
        )

        # Verify embedding vectors exist
        r = await conn.execute(
            text(
                "SELECT count(*) FROM rag_chunks "
                "WHERE document_id = :did AND embedding_text_embedding_v4_1024 IS NOT NULL"
            ),
            {"did": doc_id},
        )
        embedded_count = r.scalar()
        assert embedded_count > 0, "No embedding vectors found in child chunks"

    await engine.dispose()


# ── Tests ──────────────────────────────────────────────────


async def test_ingest_markdown_document(client: httpx.AsyncClient, settings: Settings):
    source_uri = f"{TEST_SOURCE_URI_PREFIX}refund-policy-{uuid.uuid4().hex[:8]}.md"
    response = await client.post(
        "/api/knowledge/documents",
        files={"file": ("refund-policy.md", MARKDOWN_CONTENT.encode("utf-8"), "text/markdown")},
        data={"source_uri": source_uri, "title": "退款政策"},
    )
    assert response.status_code == 200, f"body: {response.text}"
    body = response.json()

    assert body["status"] == "success"
    assert body["document_id"] is not None
    assert body["job_id"] is not None
    assert body["embedding_status"] in ("succeeded", "not_requested", "not_configured")

    # Verify ingest job is retrievable
    job_response = await client.get(f"/api/knowledge/ingest-jobs/{body['job_id']}")
    assert job_response.status_code == 200
    job = job_response.json()
    assert job["status"] == "success"
    assert job["source_uri"] == source_uri
    assert job["parent_chunk_count"] > 0
    assert job["child_chunk_count"] > 0

    # Verify database records and embeddings
    await _verify_document_in_db(settings, source_uri, expected_child_chunks=job["child_chunk_count"])


async def test_ingest_txt_document(client: httpx.AsyncClient, settings: Settings):
    source_uri = f"{TEST_SOURCE_URI_PREFIX}intro-{uuid.uuid4().hex[:8]}.txt"
    response = await client.post(
        "/api/knowledge/documents",
        files={"file": ("intro.txt", TXT_CONTENT.encode("utf-8"), "text/plain")},
        data={"source_uri": source_uri},
    )
    assert response.status_code == 200, f"body: {response.text}"
    body = response.json()

    assert body["status"] == "success"
    assert body["document_id"] is not None


async def test_ingest_duplicate_document(client: httpx.AsyncClient, settings: Settings):
    source_uri = f"{TEST_SOURCE_URI_PREFIX}dedup-{uuid.uuid4().hex[:8]}.md"

    response1 = await client.post(
        "/api/knowledge/documents",
        files={"file": ("dedup.md", MARKDOWN_CONTENT.encode("utf-8"), "text/markdown")},
        data={"source_uri": source_uri},
    )
    assert response1.status_code == 200
    assert response1.json()["status"] == "success"

    response2 = await client.post(
        "/api/knowledge/documents",
        files={"file": ("dedup.md", MARKDOWN_CONTENT.encode("utf-8"), "text/markdown")},
        data={"source_uri": source_uri},
    )
    assert response2.status_code == 200
    assert response2.json()["status"] == "skipped_duplicate"


async def test_ingest_rejects_forbidden_field(client: httpx.AsyncClient):
    source_uri = f"{TEST_SOURCE_URI_PREFIX}bad-{uuid.uuid4().hex[:8]}.md"
    response = await client.post(
        "/api/knowledge/documents",
        files={"file": ("bad.md", b"# Bad", "text/markdown")},
        data={"source_uri": source_uri, "tenant_id": "hacker"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "forbidden_field"


async def test_get_ingest_job_not_found(client: httpx.AsyncClient):
    fake_id = str(uuid.uuid4())
    response = await client.get(f"/api/knowledge/ingest-jobs/{fake_id}")
    assert response.status_code == 404
