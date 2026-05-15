"""FastAPI app factory for RecallForge M5."""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from recallforge.config import Settings, get_config
from recallforge.context import current_request_context

from .errors import ApiError, ValidationApiError, error_body
from .routes import console, health, knowledge, knowledge_bases, rag


def create_app(
    settings: Settings | None = None,
    *,
    knowledge_service: Any | None = None,
    governance_service: Any | None = None,
    session_factory: Any | None = None,
) -> FastAPI:
    settings = settings or get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.upload_startup_cleanup_enabled:
            cleanup_upload_temp_dir(settings)
        try:
            yield
        finally:
            engine = getattr(app.state, "engine", None)
            if engine is not None:
                await engine.dispose()

    app = FastAPI(
        title=settings.api_title,
        docs_url="/docs" if settings.api_docs_enabled else None,
        redoc_url="/redoc" if settings.api_docs_enabled else None,
        openapi_url="/openapi.json" if settings.api_openapi_enabled else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    if knowledge_service is not None:
        app.state.knowledge_service = knowledge_service
    if governance_service is not None:
        app.state.governance_service = governance_service
    if session_factory is not None:
        app.state.session_factory = session_factory
    origins = [item.strip() for item in settings.api_cors_allowed_origins.split(",") if item.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-API-Key", settings.api_request_id_header],
        )

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_body(exc, _trace_id()))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = json.loads(json.dumps(exc.errors(), default=str))
        error_text = str(errors)
        if "forbidden filter field" in error_text:
            code = "forbidden_filter"
        elif "forbidden identity" in error_text or "forbidden field" in error_text:
            code = "forbidden_field"
        else:
            code = "validation_error"
        error = ValidationApiError(code, "request validation failed", {"errors": errors})
        return JSONResponse(status_code=400, content=error_body(error, _trace_id()))

    app.include_router(health.router)
    app.include_router(knowledge_bases.router)
    app.include_router(knowledge.router)
    app.include_router(rag.router)
    app.include_router(console.router)
    return app


def cleanup_upload_temp_dir(settings: Settings) -> None:
    root = Path(settings.upload_temp_dir)
    if not root.exists():
        return
    try:
        root = root.resolve()
    except OSError:
        return
    cutoff = time.time() - settings.upload_temp_ttl_seconds
    for path in root.rglob("*"):
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.is_file() or root not in resolved.parents:
            continue
        try:
            if resolved.stat().st_mtime < cutoff:
                resolved.unlink()
        except OSError:
            continue


def _trace_id() -> str | None:
    ctx = current_request_context.get()
    return str(ctx.request_id) if ctx else None
