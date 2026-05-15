from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from recallforge.api.answering import AnswerGenerationResult
from recallforge.api.knowledge_service import KnowledgeService
from recallforge.api.schemas import AnswerRequest, DocumentUploadCommand, RetrieveRequest
from recallforge.config import Settings
from recallforge.context import RequestContext
from recallforge.retrieval.types import HitSummary, Reference, ReferenceChild, RetrievalResult


class FakeIngestService:
    def __init__(self, status="success"):
        self.status = status
        self.last_request = None

    async def ingest_document(self, request):
        self.last_request = request
        return SimpleNamespace(
            document_id=42,
            job_id=uuid.uuid4(),
            status=self.status,
        )


class FakeRetrievalService:
    def __init__(self, result):
        self.result = result
        self.requests = []

    async def retrieve(self, request, ctx):
        self.requests.append(request)
        return self.result


class FakeAnswerGenerator:
    def __init__(self, answer="The answer is in the handbook [1]."):
        self.answer = answer
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return AnswerGenerationResult(
            answer=self.answer,
            metadata={"answer_validation": {"valid": True}},
        )


class FakeSession:
    def __init__(self):
        self.updated_answers = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def begin(self):
        return self


class FakeSessionFactory:
    def __init__(self):
        self.session = FakeSession()

    def __call__(self):
        return self.session


class FakeQueryLogRepo:
    def __init__(self, session):
        self.session = session

    async def update_answer(self, request_id, tenant_id, answer):
        self.session.updated_answers.append((request_id, tenant_id, answer))
        return SimpleNamespace()


def _settings(**overrides) -> Settings:
    data = {
        "openai_api_key": "test",
        "auto_embedding_backfill_on_ingest": False,
        "answer_generation_enabled": True,
    }
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
async def test_ingest_uses_request_context_identity(tmp_path: Path):
    ingest = FakeIngestService()
    service = KnowledgeService(
        settings=_settings(),
        ingest_service=ingest,
        retrieval_service_provider=lambda: FakeRetrievalService(_retrieved()),
    )

    result = await service.ingest_document(
        DocumentUploadCommand(
            file_path=tmp_path / "doc.md",
            source_uri="docs/doc.md",
            cleanup_file=False,
        ),
        _ctx(),
    )

    assert result.status == "success"
    assert ingest.last_request.tenant_id == "tenant-a"
    assert ingest.last_request.department == "engineering"
    assert ingest.last_request.access_level == "restricted"


@pytest.mark.asyncio
async def test_retrieve_calls_retrieval_service_and_marks_date_range_warning():
    retrieval = FakeRetrievalService(_retrieved())
    service = KnowledgeService(
        settings=_settings(),
        ingest_service=FakeIngestService(),
        retrieval_service_provider=lambda: retrieval,
    )

    result = await service.retrieve(
        RetrieveRequest(question="refund policy", filters={"date_range": {"from": "2026-01-01"}}),
        _ctx(),
    )

    assert result.status == "retrieved"
    assert retrieval.requests
    assert "date_range_filter_ignored" in result.metadata["warnings"]


@pytest.mark.asyncio
async def test_answer_updates_existing_query_log_after_generation():
    session_factory = FakeSessionFactory()
    generator = FakeAnswerGenerator()
    service = KnowledgeService(
        settings=_settings(),
        ingest_service=FakeIngestService(),
        retrieval_service_provider=lambda: FakeRetrievalService(_retrieved()),
        session_factory=session_factory,
        answer_generator=generator,
        query_log_repo_type=FakeQueryLogRepo,
    )
    ctx = _ctx()

    result = await service.answer(AnswerRequest(question="refund policy"), ctx)

    assert result.status == "success"
    assert result.answer.endswith("[1].")
    assert session_factory.session.updated_answers == [(ctx.request_id, "tenant-a", result.answer)]


@pytest.mark.asyncio
async def test_answer_refusal_does_not_call_generator():
    generator = FakeAnswerGenerator()
    service = KnowledgeService(
        settings=_settings(),
        ingest_service=FakeIngestService(),
        retrieval_service_provider=lambda: FakeRetrievalService(RetrievalResult(status="refused", refusal_reason="no_candidates")),
        answer_generator=generator,
    )

    result = await service.answer(AnswerRequest(question="unknown"), _ctx())

    assert result.status == "refused"
    assert generator.requests == []


def _retrieved() -> RetrievalResult:
    return RetrievalResult(
        status="retrieved",
        context_text="[1] parent context",
        references=[_reference()],
        hit_summary=[
            HitSummary(
                chunk_id=1,
                document_id=42,
                parent_id=7,
                chunk_key="c-1",
                parent_key="p-1",
                vector_rank=1,
                vector_score=0.8,
                rerank_rank=1,
                rerank_score=0.9,
                score_source="rerank",
                selected=True,
            )
        ],
    )


def _reference() -> Reference:
    return Reference(
        ref_id="[1]",
        index=1,
        document_id=42,
        document_title="Handbook",
        chunk_id=1,
        chunk_key="c-1",
        parent_id=7,
        parent_key="p-1",
        source_uri="docs/doc.md",
        doc_type="markdown",
        page_start=1,
        page_end=1,
        heading_path=["Intro"],
        version=1,
        rerank_score=0.9,
        vector_score=0.8,
        child_chunks=[
            ReferenceChild(
                chunk_id=1,
                chunk_key="c-1",
                rerank_score=0.9,
                rerank_rank=1,
                page_start=1,
                page_end=1,
            )
        ],
    )
