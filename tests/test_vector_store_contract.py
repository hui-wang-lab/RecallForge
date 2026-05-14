"""M3 VectorStoreAdapter contract tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from recallforge.embeddings.provider import EmbeddingDimensionMismatch
from recallforge.storage.pgvector_store import PgVectorStore
from recallforge.storage.vector_store import (
    VectorChunk,
    VectorFilterError,
    VectorMetadataError,
    VectorSearchError,
    VectorSearchFilter,
    UnsupportedSearchModeError,
)


def _vector_chunk(**overrides) -> VectorChunk:
    data = dict(
        chunk_id=1,
        tenant_id="tenant-a",
        document_id=10,
        parent_id=20,
        chunk_key="child-1",
        parent_key="parent-1",
        embedding=[0.1, 0.2, 0.3],
        embedding_provider="dashscope",
        embedding_model="text-embedding-v4@1024",
        embedding_dim=3,
        metadata={"doc_type": "markdown", "status": "active"},
    )
    data.update(overrides)
    return VectorChunk(**data)


def test_vector_chunk_rejects_wrong_dimension():
    with pytest.raises(EmbeddingDimensionMismatch):
        _vector_chunk(embedding_dim=4)


def test_vector_chunk_rejects_user_id_metadata():
    with pytest.raises(VectorMetadataError, match="user_id"):
        _vector_chunk(metadata={"user_id": "u1"})


@pytest.mark.asyncio
async def test_search_rejects_unsupported_mode_before_db_call():
    session = AsyncMock()
    store = PgVectorStore(session)

    with pytest.raises(UnsupportedSearchModeError):
        await store.search(
            [0.0] * 1024,
            "text-embedding-v4@1024",
            VectorSearchFilter(tenant_id="tenant-a"),
            10,
            "hybrid",
        )

    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_search_requires_positive_top_k_before_db_call():
    session = AsyncMock()
    store = PgVectorStore(session)

    with pytest.raises(VectorSearchError):
        await store.search([0.0] * 1024, "text-embedding-v4@1024", VectorSearchFilter(tenant_id="tenant-a"), 0)

    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_search_requires_tenant_filter_before_db_call():
    session = AsyncMock()
    store = PgVectorStore(session)

    with pytest.raises(VectorFilterError):
        await store.search([0.0] * 1024, "text-embedding-v4@1024", VectorSearchFilter(tenant_id=""), 10)

    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_search_rejects_query_dimension_mismatch_before_db_call():
    session = AsyncMock()
    store = PgVectorStore(session)

    with pytest.raises(EmbeddingDimensionMismatch):
        await store.search([0.0, 1.0], "text-embedding-v4@1024", VectorSearchFilter(tenant_id="tenant-a"), 10)

    session.execute.assert_not_called()
