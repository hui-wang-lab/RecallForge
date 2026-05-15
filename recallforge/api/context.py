"""FastAPI helpers for request-scoped RecallForge context."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from recallforge.context import RequestContext, current_request_context


@contextmanager
def request_context_scope(ctx: RequestContext) -> Iterator[RequestContext]:
    token = current_request_context.set(ctx)
    try:
        yield ctx
    finally:
        current_request_context.reset(token)
