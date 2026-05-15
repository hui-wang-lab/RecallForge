"""Compatibility aliases under /api/rag."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from recallforge.api.auth import AuthenticatedRequest, require_scopes
from recallforge.api.dependencies import get_knowledge_service, get_settings
from recallforge.api.knowledge_service import KnowledgeService
from recallforge.api.routes.knowledge import handle_document_upload
from recallforge.api.schemas import AnswerResponse, DocumentIngestResponse, RagQueryRequest
from recallforge.config import Settings

router = APIRouter(prefix="/api/rag", tags=["rag"])


@router.post(
    "/documents",
    response_model=DocumentIngestResponse,
    summary="Upload and ingest a document through the RAG alias",
    description="Compatibility alias for /api/knowledge/documents; uses the same handler and service path.",
)
async def upload_rag_document(
    request: Request,
    auth: AuthenticatedRequest = Depends(require_scopes("documents:write")),
    service: KnowledgeService = Depends(get_knowledge_service),
    settings: Settings = Depends(get_settings),
) -> DocumentIngestResponse:
    return await handle_document_upload(request, auth, service, settings)


@router.post(
    "/query",
    response_model=AnswerResponse,
    summary="Run a citation-bound RAG answer",
    description="Compatibility alias for the Knowledge answer flow; request body only accepts question and filters.",
)
async def rag_query(
    payload: RagQueryRequest,
    auth: AuthenticatedRequest = Depends(require_scopes("knowledge:answer")),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> AnswerResponse:
    return await service.answer(payload, auth.context)
