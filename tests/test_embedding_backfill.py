"""M3 embedding backfill tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from recallforge.config import Settings
from recallforge.embeddings.backfill import BackfillRequest, EmbeddingBackfillService
from recallforge.storage.embedding_columns import EmbeddingColumnRegistry, EmbeddingColumnSpec
from recallforge.storage.repository import ChildChunkEmbeddingSource


@dataclass
class _FakeProvider:
    provider: str = "dashscope"
    name: str = "text-embedding-v4@1024"
    model_slug: str = "text_embedding_v4_1024"
    dim: int = 3
    max_input_tokens: int = 8192
    distance_metric: str = "cosine"
    last_retry_count: int = 1

    async def embed_documents(self, texts):
        return [[float(index), 0.0, 1.0] for index, _ in enumerate(texts)]

    async def embed_query(self, text):
        return [0.0, 0.0, 1.0]

    async def preflight(self):
        return None


class _FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeSession:
    def begin(self):
        return _FakeTx()


class _FakeSessionContext:
    async def __aenter__(self):
        return _FakeSession()

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeRepository:
    def __init__(self, sources):
        self.sources = sources

    async def list_for_embedding_backfill(self, *args, **kwargs):
        return self.sources


class _FakeVectorStore:
    def __init__(self):
        self.upserted = []

    async def upsert_chunks(self, chunks):
        self.upserted.extend(chunks)


def _source(chunk_id: int) -> ChildChunkEmbeddingSource:
    return ChildChunkEmbeddingSource(
        id=chunk_id,
        tenant_id="tenant-a",
        document_id=10,
        parent_id=20,
        chunk_key=f"child-{chunk_id}",
        parent_key="parent-1",
        content=f"content {chunk_id}",
        doc_type="markdown",
        chunk_type="child",
        template="generic_structured",
        department="eng",
        access_level="internal",
        heading_path=["Guide"],
        page_start=1,
        page_end=1,
        source_uri="file:///guide.md",
        version=1,
        status="active",
    )


@pytest.mark.asyncio
async def test_embedding_backfill_builds_vector_chunks_without_direct_db_writes():
    sources = [_source(1), _source(2)]
    vector_store = _FakeVectorStore()
    columns = EmbeddingColumnRegistry(
        [
            EmbeddingColumnSpec(
                provider="dashscope",
                model="text-embedding-v4@1024",
                model_slug="text_embedding_v4_1024",
                column_name="embedding_text_embedding_v4_1024",
                dim=3,
                distance_metric="cosine",
            )
        ]
    )
    service = EmbeddingBackfillService(
        session_factory=lambda: _FakeSessionContext(),
        provider=_FakeProvider(),
        settings=Settings(openai_api_key="test", embedding_batch_size=10),
        columns=columns,
        repository_factory=lambda session: _FakeRepository(sources),
        vector_store_factory=lambda session, columns: vector_store,
    )

    result = await service.backfill(BackfillRequest(embedding_model="text-embedding-v4@1024", limit=10))

    assert result.attempted == 2
    assert result.succeeded == 2
    assert result.failed == 0
    assert [chunk.chunk_id for chunk in vector_store.upserted] == [1, 2]
    assert vector_store.upserted[0].metadata["tenant_id"] == "tenant-a"
    assert vector_store.upserted[0].metadata["embedding_dim"] == 3
    assert "user_id" not in vector_store.upserted[0].metadata
