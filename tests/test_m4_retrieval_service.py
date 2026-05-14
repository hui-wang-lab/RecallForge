from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from recallforge.config import Settings
from recallforge.context import RequestContext
from recallforge.retrieval.errors import RerankerConfigurationError
from recallforge.retrieval.reranker.provider import RerankedCandidate
from recallforge.retrieval.retrieval_service import RetrievalService
from recallforge.retrieval.types import RetrievalRequest
from recallforge.storage.vector_store import VectorSearchHit


class FakeEmbeddingProvider:
    provider = "dashscope"
    name = "text-embedding-v4@1024"
    model_slug = "text_embedding_v4_1024"
    dim = 3
    max_input_tokens = 8192
    distance_metric = "cosine"

    async def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


class FakeVectorStore:
    def __init__(self, hits=None):
        self.hits = hits if hits is not None else [_hit(1, 0.8), _hit(2, 0.7)]
        self.filters = None

    async def search(self, query_embedding, embedding_model, filters, top_k, search_mode="vector"):
        self.filters = filters
        return self.hits[:top_k]


class FakeReranker:
    provider = "dashscope"
    name = "qwen3-rerank"
    max_candidates = 500

    async def rerank(self, query, candidates, top_k=None):
        return [
            RerankedCandidate(
                chunk_id=candidate.chunk_id,
                rerank_score=0.9 - (index * 0.1),
                rerank_rank=index + 1,
                original_rank=candidate.original_rank,
                original_score=candidate.original_score,
            )
            for index, candidate in enumerate(candidates[: top_k or len(candidates)])
        ]


class FailingReranker(FakeReranker):
    async def rerank(self, query, candidates, top_k=None):
        raise RuntimeError("rerank down")


class FakeChunkRepo:
    def __init__(self, session):
        pass

    async def get_by_ids(self, tenant_id, chunk_ids, statuses=("active",)):
        return [
            SimpleNamespace(
                id=chunk_id,
                content=f"child content {chunk_id}",
            )
            for chunk_id in chunk_ids
        ]


class FakeParentRepo:
    def __init__(self, session):
        pass

    async def get_by_ids(self, tenant_id, parent_ids, statuses=("active",)):
        return [
            SimpleNamespace(
                id=parent_id,
                tenant_id=tenant_id,
                document_id=100,
                source_uri="handbook.md",
                doc_type="markdown",
                parent_key=f"p-{parent_id}",
                content=f"parent content {parent_id} child content 1 child content 2",
                token_count=20,
                heading_path=["Handbook"],
                page_start=1,
                page_end=2,
                version=1,
            )
            for parent_id in parent_ids
        ]


class FakeDocRepo:
    def __init__(self, session):
        pass

    async def get_by_ids(self, tenant_id, document_ids, statuses=("active",)):
        return [SimpleNamespace(id=document_id, title="Handbook") for document_id in document_ids]


class FakeQueryLogRepo:
    records = []

    def __init__(self, session):
        pass

    async def create(self, input):
        self.__class__.records.append(input)
        return input


def _settings(**overrides) -> Settings:
    data = {"openai_api_key": "test-key", "reranker_required": False, "embedding_dim": 3, "min_vector_score": 0.6}
    data.update(overrides)
    return Settings(**data)


def _ctx() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-a",
        user_id="user-1",
        department="engineering",
        access_level="restricted",
        request_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_retrieval_service_happy_path_writes_retrieved_log():
    FakeQueryLogRepo.records = []
    vector_store = FakeVectorStore()
    service = _service(_settings(), vector_store, FakeReranker())

    result = await service.retrieve(RetrievalRequest("refund policy"), _ctx())

    assert result.status == "retrieved"
    assert result.references
    assert vector_store.filters.tenant_id == "tenant-a"
    assert vector_store.filters.access_level == ["public", "internal", "confidential", "restricted"]
    assert FakeQueryLogRepo.records[-1].status == "retrieved"
    assert FakeQueryLogRepo.records[-1].answer is None


@pytest.mark.asyncio
async def test_retrieval_service_rejects_forbidden_filter_without_recall():
    FakeQueryLogRepo.records = []
    vector_store = FakeVectorStore()
    service = _service(_settings(), vector_store, FakeReranker())

    result = await service.retrieve(RetrievalRequest("refund policy", {"tenant_id": "*"}), _ctx())

    assert result.status == "failed"
    assert "forbidden" in result.error_message
    assert vector_store.filters is None
    assert FakeQueryLogRepo.records[-1].status == "failed"


@pytest.mark.asyncio
async def test_retrieval_service_refuses_when_no_candidates():
    FakeQueryLogRepo.records = []
    service = _service(_settings(), FakeVectorStore(hits=[]), FakeReranker())

    result = await service.retrieve(RetrievalRequest("refund policy"), _ctx())

    assert result.status == "refused"
    assert result.refusal_reason == "no_candidates"
    assert FakeQueryLogRepo.records[-1].status == "refused"


@pytest.mark.asyncio
async def test_retrieval_service_reranker_failure_falls_back_to_vector():
    FakeQueryLogRepo.records = []
    service = _service(_settings(), FakeVectorStore(), FailingReranker())

    result = await service.retrieve(RetrievalRequest("refund policy"), _ctx())

    assert result.status == "retrieved"
    assert result.metadata["reranker_fallback"] is True
    assert any(summary.score_source == "vector" for summary in result.hit_summary)


def test_reranker_required_blocks_service_startup():
    with pytest.raises(RerankerConfigurationError):
        _service(_settings(reranker_required=True), FakeVectorStore(), None)


def _service(settings, vector_store, reranker):
    return RetrievalService(
        settings=settings,
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=vector_store,
        reranker=reranker,
        session=SimpleNamespace(),
        parent_repo_type=FakeParentRepo,
        chunk_repo_type=FakeChunkRepo,
        query_log_repo_type=FakeQueryLogRepo,
        doc_repo_type=FakeDocRepo,
    )


def _hit(chunk_id: int, score: float) -> VectorSearchHit:
    return VectorSearchHit(
        chunk_id=chunk_id,
        document_id=100,
        parent_id=200,
        chunk_key=f"c-{chunk_id}",
        parent_key="p-200",
        rank=chunk_id,
        score=score,
        distance=1 - score,
        score_source="vector",
        metadata={
            "tenant_id": "tenant-a",
            "document_id": 100,
            "chunk_id": chunk_id,
            "chunk_key": f"c-{chunk_id}",
            "parent_id": 200,
            "parent_key": "p-200",
            "doc_type": "markdown",
            "chunk_type": "child",
            "template": "generic",
            "access_level": "public",
            "department": "global",
            "heading_path": ["Handbook"],
            "page_start": 1,
            "page_end": 2,
            "source_uri": "handbook.md",
            "version": 1,
            "embedding_model": "text-embedding-v4@1024",
            "embedding_provider": "dashscope",
            "embedding_dim": 3,
            "status": "active",
        },
    )
