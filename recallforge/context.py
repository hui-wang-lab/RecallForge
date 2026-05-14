"""Request-scoped security context for retrieval and agent tools."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RequestContext:
    tenant_id: str
    user_id: str
    department: str
    access_level: str
    request_id: uuid.UUID = field(default_factory=uuid.uuid4)


current_request_context: ContextVar[RequestContext | None] = ContextVar(
    "current_request_context",
    default=None,
)
