from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from recallforge.api.app import create_app
from recallforge.api.schemas import KnowledgeBaseListResponse, KnowledgeBaseResponse
from recallforge.config import Settings


class FakeGovernanceService:
    def __init__(self):
        self.created = []

    async def create_knowledge_base(self, payload, ctx):
        self.created.append(payload)
        return _kb_response(ctx)

    async def list_knowledge_bases(self, ctx, **kwargs):
        return KnowledgeBaseListResponse(items=[_kb_response(ctx)], trace_id=str(ctx.request_id))


def _settings() -> Settings:
    return Settings(openai_api_key="test", api_require_auth=False)


def _kb_response(ctx):
    return KnowledgeBaseResponse(
        knowledge_base_id=1,
        name="Product",
        status="active",
        role="owner",
        tags=[],
        default_department="global",
        default_access_level="internal",
        default_parser="auto",
        default_template="auto",
        default_search_mode="vector",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        trace_id=str(ctx.request_id),
    )


def test_create_knowledge_base_route_uses_governance_service():
    service = FakeGovernanceService()
    app = create_app(_settings(), governance_service=service)

    response = TestClient(app).post("/api/knowledge-bases", json={"name": "Product"})

    assert response.status_code == 200
    assert response.json()["knowledge_base_id"] == 1
    assert service.created


def test_list_knowledge_bases_route():
    app = create_app(_settings(), governance_service=FakeGovernanceService())

    response = TestClient(app).get("/api/knowledge-bases")

    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "Product"
