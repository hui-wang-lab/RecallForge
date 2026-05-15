"""Server-side metadata filter construction for M4 retrieval."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from recallforge.config import Settings
from recallforge.context import RequestContext
from recallforge.retrieval.errors import FilterBuilderError
from recallforge.storage.vector_store import VectorSearchFilter

AuditLogHook = Callable[[str, dict[str, Any]], None]

CLIENT_FILTER_WHITELIST = frozenset(
    {"doc_type", "source_uri", "version", "date_range", "knowledge_base_id", "knowledge_base_ids"}
)
FORBIDDEN_CLIENT_KEYS = frozenset({"tenant_id", "user_id", "department", "access_level", "status"})
ACCESS_LEVEL_MATRIX = {
    "public": ["public"],
    "internal": ["public", "internal"],
    "confidential": ["public", "internal", "confidential"],
    "restricted": ["public", "internal", "confidential", "restricted"],
}


def default_audit_log_hook(event: str, context: dict[str, Any]) -> None:
    logging.getLogger("recallforge.audit").warning("audit_event=%s context=%s", event, context)


class FilterBuilder:
    def __init__(
        self,
        settings: Settings,
        audit_hook: AuditLogHook | None = None,
    ) -> None:
        self._settings = settings
        self._audit_hook = audit_hook or default_audit_log_hook

    def build(self, ctx: RequestContext, client_filters: dict[str, Any] | None) -> VectorSearchFilter:
        filters = dict(client_filters or {})
        forbidden = sorted(set(filters) & FORBIDDEN_CLIENT_KEYS)
        if forbidden:
            self._audit(
                "client_filter_forbidden",
                ctx,
                {"forbidden_keys": forbidden},
            )
            raise FilterBuilderError(f"client filters contain forbidden keys: {forbidden}")

        unknown = sorted(set(filters) - CLIENT_FILTER_WHITELIST)
        if unknown:
            self._audit("client_filter_unknown", ctx, {"unknown_keys": unknown})
            raise FilterBuilderError(f"client filters contain unknown keys: {unknown}")

        access_levels = ACCESS_LEVEL_MATRIX.get(ctx.access_level)
        if access_levels is None:
            raise FilterBuilderError(f"invalid request access_level: {ctx.access_level!r}")

        departments = [ctx.department] if ctx.department == "global" else [ctx.department, "global"]
        version = filters.get("version")
        if version is not None and not isinstance(version, int):
            raise FilterBuilderError("client filter 'version' must be an integer")
        knowledge_base_id = _knowledge_base_filter(filters)

        return VectorSearchFilter(
            tenant_id=ctx.tenant_id,
            knowledge_base_id=knowledge_base_id,
            department=departments,
            access_level=access_levels,
            doc_type=_optional_string(filters.get("doc_type"), "doc_type"),
            source_uri=_optional_string(filters.get("source_uri"), "source_uri"),
            version=version,
            status="active",
        )

    def _audit(self, event: str, ctx: RequestContext, extra: dict[str, Any]) -> None:
        try:
            payload = {
                "tenant_id": ctx.tenant_id,
                "user_id": ctx.user_id,
                "request_id": str(ctx.request_id),
            }
            payload.update(extra)
            self._audit_hook(event, payload)
        except Exception:  # pragma: no cover - audit hooks must not break retrieval
            logging.getLogger("recallforge.audit").exception("audit hook failed")


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise FilterBuilderError(f"client filter {field_name!r} must be a non-empty string")
    return value


def _knowledge_base_filter(filters: dict[str, Any]) -> int | list[int] | None:
    single = filters.get("knowledge_base_id")
    many = filters.get("knowledge_base_ids")
    if single is not None and many is not None:
        raise FilterBuilderError("use either 'knowledge_base_id' or 'knowledge_base_ids', not both")
    if single is not None:
        if not isinstance(single, int) or single <= 0:
            raise FilterBuilderError("client filter 'knowledge_base_id' must be a positive integer")
        return single
    if many is not None:
        if not isinstance(many, list) or not many:
            raise FilterBuilderError("client filter 'knowledge_base_ids' must be a non-empty list")
        ids = []
        for item in many:
            if not isinstance(item, int) or item <= 0:
                raise FilterBuilderError("client filter 'knowledge_base_ids' must contain positive integers")
            ids.append(item)
        return ids
    return None
