"""M6 knowledge-base governance routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from recallforge.api.auth import AuthenticatedRequest, require_any_scope, require_scopes
from recallforge.api.dependencies import get_governance_service, get_settings
from recallforge.api.governance_service import GovernanceService
from recallforge.api.routes.knowledge import _metadata_from_form, _optional_form_str, _save_upload
from recallforge.api.schemas import (
    FORBIDDEN_IDENTITY_FIELDS,
    DocumentDeleteResponse,
    DocumentIngestResponse,
    DocumentListResponse,
    DocumentSummaryResponse,
    DocumentUpdateRequest,
    DocumentUploadCommand,
    IngestJobResponse,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseDeleteRequest,
    KnowledgeBaseListResponse,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdateRequest,
    ReindexRequest,
    ReindexResponse,
    validate_metadata,
)
from recallforge.config import Settings

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge-bases"])


@router.post("", response_model=KnowledgeBaseResponse)
async def create_knowledge_base(
    payload: KnowledgeBaseCreateRequest,
    auth: AuthenticatedRequest = Depends(require_any_scope("knowledge_bases:write", "documents:write")),
    service: GovernanceService = Depends(get_governance_service),
) -> KnowledgeBaseResponse:
    return await service.create_knowledge_base(payload, auth.context)


@router.get("", response_model=KnowledgeBaseListResponse)
async def list_knowledge_bases(
    status: str = "active",
    q: str | None = None,
    tag: str | None = None,
    limit: int | None = Query(default=None, gt=0),
    auth: AuthenticatedRequest = Depends(require_any_scope("knowledge_bases:read", "knowledge:read")),
    service: GovernanceService = Depends(get_governance_service),
) -> KnowledgeBaseListResponse:
    return await service.list_knowledge_bases(auth.context, status=status, q=q, tag=tag, limit=limit)


@router.get("/{kb_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(
    kb_id: int,
    auth: AuthenticatedRequest = Depends(require_any_scope("knowledge_bases:read", "knowledge:read")),
    service: GovernanceService = Depends(get_governance_service),
) -> KnowledgeBaseResponse:
    return await service.get_knowledge_base(kb_id, auth.context)


@router.patch("/{kb_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    kb_id: int,
    payload: KnowledgeBaseUpdateRequest,
    auth: AuthenticatedRequest = Depends(require_any_scope("knowledge_bases:write", "documents:write")),
    service: GovernanceService = Depends(get_governance_service),
) -> KnowledgeBaseResponse:
    return await service.update_knowledge_base(kb_id, payload, auth.context)


@router.delete("/{kb_id}", response_model=KnowledgeBaseResponse)
async def delete_knowledge_base(
    kb_id: int,
    payload: KnowledgeBaseDeleteRequest,
    auth: AuthenticatedRequest = Depends(require_any_scope("knowledge_bases:write", "documents:write")),
    service: GovernanceService = Depends(get_governance_service),
) -> KnowledgeBaseResponse:
    return await service.delete_knowledge_base(kb_id, payload, auth.context)


@router.post("/{kb_id}/documents", response_model=DocumentIngestResponse)
async def upload_document(
    kb_id: int,
    request: Request,
    auth: AuthenticatedRequest = Depends(require_scopes("documents:write")),
    service: GovernanceService = Depends(get_governance_service),
    settings: Settings = Depends(get_settings),
) -> DocumentIngestResponse:
    form = await request.form()
    forbidden = sorted(set(form.keys()) & FORBIDDEN_IDENTITY_FIELDS)
    if forbidden:
        from recallforge.api.errors import ValidationApiError

        raise ValidationApiError("forbidden_field", f"request body contains forbidden field: {forbidden[0]}")
    upload = form.get("file")
    from starlette.datastructures import UploadFile as StarletteUploadFile

    if not isinstance(upload, StarletteUploadFile):
        from recallforge.api.errors import ValidationApiError

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
        knowledge_base_id=kb_id,
        cleanup_file=True,
    )
    return await service.upload_document(kb_id, command, auth.context)


@router.get("/{kb_id}/documents", response_model=DocumentListResponse)
async def list_documents(
    kb_id: int,
    status: str = "active",
    doc_type: str | None = None,
    q: str | None = None,
    limit: int | None = Query(default=None, gt=0),
    auth: AuthenticatedRequest = Depends(require_any_scope("documents:read", "knowledge:read")),
    service: GovernanceService = Depends(get_governance_service),
) -> DocumentListResponse:
    return await service.list_documents(kb_id, auth.context, status=status, doc_type=doc_type, q=q, limit=limit)


@router.get("/{kb_id}/documents/{document_id}", response_model=DocumentSummaryResponse)
async def get_document(
    kb_id: int,
    document_id: int,
    auth: AuthenticatedRequest = Depends(require_any_scope("documents:read", "knowledge:read")),
    service: GovernanceService = Depends(get_governance_service),
):
    return await service.get_document(kb_id, document_id, auth.context)


@router.patch("/{kb_id}/documents/{document_id}", response_model=DocumentSummaryResponse)
async def update_document(
    kb_id: int,
    document_id: int,
    payload: DocumentUpdateRequest,
    auth: AuthenticatedRequest = Depends(require_scopes("documents:write")),
    service: GovernanceService = Depends(get_governance_service),
):
    return await service.update_document(kb_id, document_id, payload, auth.context)


@router.delete("/{kb_id}/documents/{document_id}", response_model=DocumentDeleteResponse)
async def delete_document(
    kb_id: int,
    document_id: int,
    auth: AuthenticatedRequest = Depends(require_scopes("documents:write")),
    service: GovernanceService = Depends(get_governance_service),
) -> DocumentDeleteResponse:
    return await service.delete_document(kb_id, document_id, auth.context)


@router.get("/{kb_id}/ingest-jobs", response_model=list[IngestJobResponse])
async def list_ingest_jobs(
    kb_id: int,
    limit: int = Query(default=50, gt=0),
    auth: AuthenticatedRequest = Depends(require_any_scope("documents:read", "documents:write")),
    service: GovernanceService = Depends(get_governance_service),
) -> list[IngestJobResponse]:
    return await service.list_ingest_jobs(kb_id, auth.context, limit=limit)


@router.post("/{kb_id}/reindex", response_model=ReindexResponse)
async def reindex_knowledge_base(
    kb_id: int,
    payload: ReindexRequest,
    auth: AuthenticatedRequest = Depends(require_scopes("documents:write")),
    service: GovernanceService = Depends(get_governance_service),
) -> ReindexResponse:
    return await service.reindex_knowledge_base(kb_id, payload, auth.context)
