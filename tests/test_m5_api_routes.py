from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from recallforge.api.app import create_app
from recallforge.api.schemas import (
    AnswerResponse,
    DocumentIngestResponse,
    IngestJobResponse,
    RetrieveResponse,
)
from recallforge.config import Settings


class FakeKnowledgeService:
    def __init__(self):
        self.ingest_calls = 0
        self.retrieve_calls = 0
        self.answer_calls = 0

    async def ingest_document(self, command, ctx):
        self.ingest_calls += 1
        command.file_path.unlink(missing_ok=True)
        return DocumentIngestResponse(
            document_id=42,
            job_id=uuid.uuid4(),
            status="success",
            embedding_status="succeeded",
            trace_id=str(ctx.request_id),
        )

    async def get_ingest_job(self, job_id, ctx):
        return IngestJobResponse(
            job_id=job_id,
            document_id=42,
            status="success",
            source_uri="docs/doc.md",
        )

    async def retrieve(self, payload, ctx):
        self.retrieve_calls += 1
        return RetrieveResponse(status="retrieved", trace_id=str(ctx.request_id))

    async def context(self, payload, ctx):
        return RetrieveResponse(status="retrieved", trace_id=str(ctx.request_id))

    async def answer(self, payload, ctx):
        self.answer_calls += 1
        return AnswerResponse(
            status="success",
            answer="Answer [1].",
            references=[],
            trace_id=str(ctx.request_id),
        )


def _settings(**overrides) -> Settings:
    data = {
        "openai_api_key": "test",
        "api_require_auth": False,
        "console_enabled": True,
        "answer_generation_enabled": True,
        "upload_temp_dir": ".tmp/test-uploads",
    }
    data.update(overrides)
    return Settings(**data)


def test_retrieve_route_uses_knowledge_service():
    service = FakeKnowledgeService()
    app = create_app(_settings(), knowledge_service=service)

    response = TestClient(app).post("/api/knowledge/retrieve", json={"question": "refund policy", "filters": {}})

    assert response.status_code == 200
    assert response.json()["status"] == "retrieved"
    assert service.retrieve_calls == 1


def test_rag_query_rejects_forbidden_filter_before_service():
    service = FakeKnowledgeService()
    app = create_app(_settings(), knowledge_service=service)

    response = TestClient(app).post(
        "/api/rag/query",
        json={"question": "refund policy", "filters": {"tenant_id": "*"}},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "forbidden_filter"
    assert service.answer_calls == 0


def test_document_upload_and_rag_alias_use_same_service_path():
    service = FakeKnowledgeService()
    app = create_app(_settings(), knowledge_service=service)
    client = TestClient(app)

    response = client.post(
        "/api/rag/documents",
        files={"file": ("doc.md", b"# Doc\ncontent", "text/markdown")},
        data={"source_uri": "docs/doc.md"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert service.ingest_calls == 1


def test_missing_auth_returns_401_when_required():
    service = FakeKnowledgeService()
    app = create_app(_settings(api_require_auth=True), knowledge_service=service)

    response = TestClient(app).post("/api/knowledge/retrieve", json={"question": "refund policy", "filters": {}})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert service.retrieve_calls == 0
