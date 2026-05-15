from __future__ import annotations

import uuid

import pytest

from recallforge.context import RequestContext
from recallforge.governance.permissions import KnowledgeBasePermissionError, KnowledgeBasePermissionService


class FakeMemberRepo:
    def __init__(self):
        self.roles = {1: "viewer", 2: "editor"}

    async def best_role(self, tenant_id, knowledge_base_id, *, user_id, department):
        return self.roles.get(knowledge_base_id)

    async def accessible_kb_ids(self, tenant_id, *, user_id, department, min_role="viewer", limit=20):
        return [1, 2][:limit]


def _ctx():
    return RequestContext(
        tenant_id="tenant-a",
        user_id="user-1",
        department="product",
        access_level="restricted",
        request_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_validate_retrieval_scope_uses_accessible_kbs_when_unspecified():
    service = KnowledgeBasePermissionService(FakeMemberRepo())

    scope = await service.validate_retrieval_scope(_ctx(), None)

    assert scope.requested_ids == []
    assert scope.effective_ids == [1, 2]


@pytest.mark.asyncio
async def test_forbidden_requested_kb_rejects_whole_scope():
    service = KnowledgeBasePermissionService(FakeMemberRepo())

    with pytest.raises(KnowledgeBasePermissionError):
        await service.validate_retrieval_scope(_ctx(), [1, 99])


@pytest.mark.asyncio
async def test_editor_can_upload_but_viewer_cannot():
    service = KnowledgeBasePermissionService(FakeMemberRepo())

    with pytest.raises(KnowledgeBasePermissionError):
        await service.require_min_role(_ctx(), 1, "editor")

    assert await service.require_min_role(_ctx(), 2, "editor") == "editor"
