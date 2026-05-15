"""Knowledge-base role checks and retrieval-scope resolution."""

from __future__ import annotations

from dataclasses import dataclass

from recallforge.context import RequestContext
from recallforge.storage.repository import KnowledgeBaseMemberRepository

ROLE_RANK = {"auditor": 0, "viewer": 1, "editor": 2, "admin": 3, "owner": 4}
ACTION_MIN_ROLE = {
    "view": "viewer",
    "retrieve": "viewer",
    "upload": "editor",
    "update_document": "editor",
    "delete_document": "editor",
    "reindex": "editor",
    "update_kb": "admin",
    "manage_members": "admin",
    "delete_kb": "owner",
}


class KnowledgeBasePermissionError(PermissionError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RetrievalScope:
    requested_ids: list[int]
    effective_ids: list[int]


class KnowledgeBasePermissionService:
    def __init__(
        self,
        member_repo: KnowledgeBaseMemberRepository,
        *,
        max_kbs_per_query: int = 20,
        allow_implicit_all: bool = True,
    ) -> None:
        self._member_repo = member_repo
        self._max_kbs_per_query = max_kbs_per_query
        self._allow_implicit_all = allow_implicit_all

    async def role_for(self, ctx: RequestContext, knowledge_base_id: int) -> str | None:
        return await self._member_repo.best_role(
            ctx.tenant_id,
            knowledge_base_id,
            user_id=ctx.user_id,
            department=ctx.department,
        )

    async def require_role(self, ctx: RequestContext, knowledge_base_id: int, allowed_roles: set[str]) -> str:
        role = await self.role_for(ctx, knowledge_base_id)
        if role is None:
            raise KnowledgeBasePermissionError("knowledge_base_not_found", "knowledge base not found")
        if role not in allowed_roles:
            raise KnowledgeBasePermissionError("insufficient_kb_role", "insufficient knowledge-base role")
        return role

    async def require_min_role(self, ctx: RequestContext, knowledge_base_id: int, min_role: str) -> str:
        role = await self.role_for(ctx, knowledge_base_id)
        if role is None:
            raise KnowledgeBasePermissionError("knowledge_base_not_found", "knowledge base not found")
        if ROLE_RANK.get(role, -1) < ROLE_RANK[min_role]:
            raise KnowledgeBasePermissionError("insufficient_kb_role", "insufficient knowledge-base role")
        return role

    async def list_accessible_kbs(self, ctx: RequestContext, action: str = "retrieve") -> list[int]:
        min_role = ACTION_MIN_ROLE.get(action, "viewer")
        return await self._member_repo.accessible_kb_ids(
            ctx.tenant_id,
            user_id=ctx.user_id,
            department=ctx.department,
            min_role=min_role,
            limit=self._max_kbs_per_query,
        )

    async def validate_retrieval_scope(
        self,
        ctx: RequestContext,
        requested_kb_ids: list[int] | None,
    ) -> RetrievalScope:
        if not requested_kb_ids:
            if not self._allow_implicit_all:
                raise KnowledgeBasePermissionError("knowledge_base_scope_required", "knowledge base scope is required")
            accessible = await self.list_accessible_kbs(ctx, "retrieve")
            if not accessible:
                raise KnowledgeBasePermissionError("knowledge_base_not_found", "no accessible knowledge bases")
            return RetrievalScope(requested_ids=[], effective_ids=accessible)

        if len(requested_kb_ids) > self._max_kbs_per_query:
            raise KnowledgeBasePermissionError("too_many_knowledge_bases", "too many knowledge bases requested")
        deduped = list(dict.fromkeys(requested_kb_ids))
        for kb_id in deduped:
            await self.require_min_role(ctx, kb_id, "viewer")
        return RetrievalScope(requested_ids=deduped, effective_ids=deduped)

    async def allowed_actions(self, ctx: RequestContext, knowledge_base_id: int) -> dict[str, bool]:
        role = await self.role_for(ctx, knowledge_base_id)
        rank = ROLE_RANK.get(role or "", -1)
        return {
            "can_view": rank >= ROLE_RANK["viewer"],
            "can_upload": rank >= ROLE_RANK["editor"],
            "can_delete_document": rank >= ROLE_RANK["editor"],
            "can_reindex": rank >= ROLE_RANK["editor"],
            "can_update": rank >= ROLE_RANK["admin"],
            "can_manage_members": rank >= ROLE_RANK["admin"],
            "can_delete": rank >= ROLE_RANK["owner"],
        }
