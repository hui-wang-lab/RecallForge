"""Audit logging facade for governance operations."""

from __future__ import annotations

from typing import Any

from recallforge.context import RequestContext
from recallforge.storage.repository import AuditEventCreate, AuditEventRepository


class AuditLogger:
    def __init__(self, repo: AuditEventRepository, *, enabled: bool = True, actor_type: str = "user") -> None:
        self._repo = repo
        self._enabled = enabled
        self._actor_type = actor_type

    async def write(
        self,
        ctx: RequestContext,
        *,
        action: str,
        resource_type: str,
        outcome: str = "success",
        knowledge_base_id: int | None = None,
        document_id: int | None = None,
        job_id: Any | None = None,
        resource_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self._enabled:
            return
        await self._repo.create(
            AuditEventCreate(
                tenant_id=ctx.tenant_id,
                actor_user_id=ctx.user_id,
                actor_type=self._actor_type,
                action=action,
                resource_type=resource_type,
                outcome=outcome,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                job_id=job_id,
                request_id=ctx.request_id,
                resource_id=resource_id,
                metadata=metadata or {},
            )
        )
