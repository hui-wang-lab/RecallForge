"""Primary /api/knowledge routes."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.datastructures import UploadFile as StarletteUploadFile

from recallforge.api.auth import AuthenticatedRequest, require_any_scope, require_scopes
from recallforge.api.dependencies import get_knowledge_service, get_settings
from recallforge.api.errors import ApiError, ValidationApiError
from recallforge.api.knowledge_service import KnowledgeService
from recallforge.api.schemas import (
    FORBIDDEN_IDENTITY_FIELDS,
    AnswerRequest,
    AnswerResponse,
    ContextRequest,
    ContextResponse,
    DocumentIngestResponse,
    DocumentUploadCommand,
    IngestJobResponse,
    RetrieveRequest,
    RetrieveResponse,
    validate_metadata,
)
from recallforge.config import Settings

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.post(
    "/documents",
    response_model=DocumentIngestResponse,
    summary="Upload and ingest a document",
)
async def upload_document(
    request: Request,
    auth: AuthenticatedRequest = Depends(require_scopes("documents:write")),
    service: KnowledgeService = Depends(get_knowledge_service),
    settings: Settings = Depends(get_settings),
) -> DocumentIngestResponse:
    return await handle_document_upload(request, auth, service, settings)


@router.get(
    "/ingest-jobs/{job_id}",
    response_model=IngestJobResponse,
    summary="Read an ingest job for the current tenant",
)
async def get_ingest_job(
    job_id: uuid.UUID,
    auth: AuthenticatedRequest = Depends(require_any_scope("documents:read", "documents:write")),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> IngestJobResponse:
    return await service.get_ingest_job(job_id, auth.context)


@router.post(
    "/retrieve",
    response_model=RetrieveResponse,
    summary="Retrieve child hits and references",
)
async def retrieve(
    payload: RetrieveRequest,
    auth: AuthenticatedRequest = Depends(require_scopes("knowledge:read")),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> RetrieveResponse:
    return await service.retrieve(payload, auth.context)


@router.post(
    "/context",
    response_model=ContextResponse,
    summary="Retrieve and assemble context",
)
async def context(
    payload: ContextRequest,
    auth: AuthenticatedRequest = Depends(require_scopes("knowledge:read")),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> ContextResponse:
    return await service.context(payload, auth.context)


@router.post(
    "/answer",
    response_model=AnswerResponse,
    summary="Generate a citation-bound test answer",
)
async def answer(
    payload: AnswerRequest,
    auth: AuthenticatedRequest = Depends(require_scopes("knowledge:answer")),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> AnswerResponse:
    return await service.answer(payload, auth.context)


async def handle_document_upload(
    request: Request,
    auth: AuthenticatedRequest,
    service: KnowledgeService,
    settings: Settings,
) -> DocumentIngestResponse:
    form = await request.form()
    forbidden = sorted(set(form.keys()) & FORBIDDEN_IDENTITY_FIELDS)
    if forbidden:
        raise ValidationApiError("forbidden_field", f"request body contains forbidden field: {forbidden[0]}")

    upload = form.get("file")
    if not isinstance(upload, StarletteUploadFile):
        raise ValidationApiError("missing_file", "multipart field 'file' is required")

    metadata = _metadata_from_form(form.get("metadata"))
    validate_metadata(metadata)
    file_path = await _save_upload(upload, settings)
    command = DocumentUploadCommand(
        file_path=file_path,
        source_uri=_optional_form_str(form.get("source_uri")) or upload.filename or file_path.name,
        source_name=_optional_form_str(form.get("source_name")) or upload.filename,
        doc_type=_optional_form_str(form.get("doc_type")),
        title=_optional_form_str(form.get("title")),
        parser_hint=_optional_form_str(form.get("parser_hint")) or "auto",
        template_hint=_optional_form_str(form.get("template_hint")) or "auto",
        metadata=metadata,
        cleanup_file=True,
    )
    return await service.ingest_document(command, auth.context)


async def _save_upload(upload: StarletteUploadFile, settings: Settings) -> Path:
    root = Path(settings.upload_temp_dir)
    if not root.is_absolute():
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    safe_name = Path(upload.filename or "upload.bin").name or "upload.bin"
    path = root / f"{uuid.uuid4()}-{safe_name}"
    written = 0
    try:
        with path.open("wb") as handle:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > settings.ingest_max_file_bytes:
                    raise ApiError(
                        "file_too_large",
                        "uploaded file exceeds ingest_max_file_bytes",
                        413,
                        {"actual": written, "limit": settings.ingest_max_file_bytes},
                    )
                handle.write(chunk)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return path


def _metadata_from_form(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValidationApiError("invalid_metadata", "metadata must be a JSON object") from exc
        if not isinstance(parsed, dict):
            raise ValidationApiError("invalid_metadata", "metadata must be a JSON object")
        return parsed
    raise ValidationApiError("invalid_metadata", "metadata must be a JSON object")


def _optional_form_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationApiError("invalid_form_field", "form fields must be strings")
    stripped = value.strip()
    return stripped or None
