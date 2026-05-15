"""M5 orchestration layer for Knowledge API endpoints."""

from __future__ import annotations

import inspect
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from recallforge.config import Settings
from recallforge.context import RequestContext
from recallforge.embeddings.backfill import BackfillRequest, EmbeddingBackfillService
from recallforge.governance.audit import AuditLogger
from recallforge.governance.permissions import KnowledgeBasePermissionError, KnowledgeBasePermissionService
from recallforge.ingest.errors import IngestError, OversizeError, ParserUnavailableError, UnsupportedFileTypeError
from recallforge.ingest.ingest_service import IngestRequest, IngestService
from recallforge.retrieval.types import RetrievalRequest, RetrievalResult
from recallforge.storage.repository import (
    AuditEventRepository,
    ChunkRepository,
    IngestJobRepository,
    KnowledgeBaseCreate,
    KnowledgeBaseMemberRepository,
    KnowledgeBaseRepository,
    QueryLogRepository,
)

from .answering import (
    REFUSAL_ANSWER,
    AnswerGenerationError,
    AnswerGenerationRequest,
    AnswerGenerator,
    validate_answer_citations,
)
from .errors import ApiError, ResourceNotFoundError, ServiceUnavailableError, ValidationApiError
from .schemas import (
    AnswerResponse,
    ContextResponse,
    DocumentIngestResponse,
    DocumentUploadCommand,
    HitSummaryResponse,
    IngestJobResponse,
    ReferenceResponse,
    RetrieveResponse,
    validate_client_filters,
)


class AsyncSessionContext(Protocol):
    async def __aenter__(self) -> AsyncSession: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> object: ...


SessionFactory = Callable[[], AsyncSessionContext]
RetrievalProvider = Callable[[], Any]


class KnowledgeService:
    def __init__(
        self,
        *,
        settings: Settings,
        ingest_service: IngestService,
        retrieval_service_provider: RetrievalProvider,
        session_factory: SessionFactory | None = None,
        backfill_service: EmbeddingBackfillService | None = None,
        answer_generator: AnswerGenerator | None = None,
        chunk_repo_type: type[ChunkRepository] = ChunkRepository,
        ingest_job_repo_type: type[IngestJobRepository] = IngestJobRepository,
        query_log_repo_type: type[QueryLogRepository] = QueryLogRepository,
    ) -> None:
        self._settings = settings
        self._ingest_service = ingest_service
        self._backfill_service = backfill_service
        self._retrieval_service_provider = retrieval_service_provider
        self._session_factory = session_factory
        self._answer_generator = answer_generator
        self._chunk_repo_type = chunk_repo_type
        self._ingest_job_repo_type = ingest_job_repo_type
        self._query_log_repo_type = query_log_repo_type

    async def ingest_document(self, command: DocumentUploadCommand, ctx: RequestContext) -> DocumentIngestResponse:
        embedding_status = "not_requested"
        metadata: dict[str, Any] = {}
        knowledge_base = await self._resolve_upload_knowledge_base(command, ctx)
        try:
            request = IngestRequest(
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                source_uri=command.source_uri,
                department=knowledge_base.default_department if knowledge_base else ctx.department,
                access_level=knowledge_base.default_access_level if knowledge_base else ctx.access_level,
                file_path=command.file_path,
                knowledge_base_id=knowledge_base.id if knowledge_base else None,
                source_name=command.source_name,
                doc_type=command.doc_type or (knowledge_base.default_doc_type if knowledge_base else None),
                title=command.title,
                created_by=ctx.user_id,
                parser_hint=command.parser_hint or (knowledge_base.default_parser if knowledge_base else "auto"),
                template_hint=command.template_hint or (knowledge_base.default_template if knowledge_base else "auto"),
                metadata=command.metadata,
            )
            job = await self._ingest_service.ingest_document(request)
            if job.status == "skipped_duplicate":
                embedding_status = "skipped_duplicate"
            elif job.status == "success":
                embedding_status, metadata = await self._maybe_backfill(job.document_id, ctx)
            return DocumentIngestResponse(
                document_id=job.document_id,
                knowledge_base_id=knowledge_base.id if knowledge_base else None,
                job_id=job.job_id,
                status=job.status,
                embedding_status=embedding_status,
                trace_id=str(ctx.request_id),
                metadata=metadata,
            )
        except OversizeError as exc:
            raise ValidationApiError(
                "file_too_large",
                exc.message,
                {"actual": exc.actual, "limit": exc.limit, "limit_name": exc.limit_name},
            ) from exc
        except UnsupportedFileTypeError as exc:
            raise ValidationApiError("unsupported_file_type", str(exc), exc.diagnostics()) from exc
        except ParserUnavailableError as exc:
            raise ServiceUnavailableError("parser_unavailable", str(exc), exc.diagnostics()) from exc
        except IngestError as exc:
            raise ValidationApiError("ingest_failed", str(exc), exc.diagnostics()) from exc
        finally:
            if command.cleanup_file and self._settings.upload_cleanup_enabled:
                _cleanup_upload(command.file_path)

    async def get_ingest_job(self, job_id: Any, ctx: RequestContext) -> IngestJobResponse:
        if self._session_factory is None:
            raise ServiceUnavailableError("repository_unavailable", "ingest job repository is not configured")
        async with self._session_factory() as session:
            repo = self._ingest_job_repo_type(session)
            job = await repo.get(job_id, ctx.tenant_id)
        if job is None:
            raise ResourceNotFoundError("ingest job not found")
        return _job_response(job)

    async def retrieve(self, request: Any, ctx: RequestContext) -> RetrieveResponse:
        result, warnings = await self._retrieve_result(request, ctx)
        if result.status == "failed":
            raise ServiceUnavailableError("retrieval_unavailable", result.error_message or "retrieval failed")
        metadata = _merge_metadata(result.metadata, warnings)
        return RetrieveResponse(
            status=result.status,
            references=_references(result),
            hit_summary=_hit_summary(result),
            refusal_reason=result.refusal_reason,
            trace_id=str(ctx.request_id),
            rewritten_query=result.rewritten_query,
            effective_query=result.effective_query,
            latencies_ms=result.latencies_ms,
            metadata=metadata,
        )

    async def context(self, request: Any, ctx: RequestContext) -> ContextResponse:
        result, warnings = await self._retrieve_result(request, ctx)
        if result.status == "failed":
            raise ServiceUnavailableError("retrieval_unavailable", result.error_message or "retrieval failed")
        metadata = _merge_metadata(result.metadata, warnings)
        return ContextResponse(
            status=result.status,
            context_text=result.context_text if result.status == "retrieved" else "",
            references=_references(result),
            hit_summary=_hit_summary(result),
            refusal_reason=result.refusal_reason,
            trace_id=str(ctx.request_id),
            rewritten_query=result.rewritten_query,
            effective_query=result.effective_query,
            latencies_ms=result.latencies_ms,
            metadata=metadata,
        )

    async def answer(self, request: Any, ctx: RequestContext) -> AnswerResponse:
        result, warnings = await self._retrieve_result(request, ctx)
        if result.status == "failed":
            raise ServiceUnavailableError("retrieval_unavailable", result.error_message or "retrieval failed")
        if result.status != "retrieved" or not result.references:
            return AnswerResponse(
                status="refused",
                answer=REFUSAL_ANSWER,
                references=[],
                refusal_reason=result.refusal_reason or "insufficient_evidence",
                trace_id=str(ctx.request_id),
                hit_summary=_hit_summary(result),
                latencies_ms=result.latencies_ms,
                metadata=_merge_metadata(result.metadata, warnings),
            )
        if not self._settings.answer_generation_enabled:
            raise ServiceUnavailableError("answer_generation_disabled", "answer generation is disabled")
        if self._answer_generator is None:
            raise ServiceUnavailableError("answer_generation_unavailable", "answer generator is not configured")

        try:
            generated = await self._answer_generator.generate(
                AnswerGenerationRequest(
                    question=request.question,
                    context_text=result.context_text,
                    references=result.references,
                )
            )
        except AnswerGenerationError as exc:
            raise ServiceUnavailableError("answer_generation_failed", str(exc)) from exc

        validation = validate_answer_citations(generated.answer, result.references)
        metadata = _merge_metadata(result.metadata, warnings)
        metadata["answer_generation"] = generated.metadata
        provider_validation = generated.metadata.get("answer_validation")
        provider_reported_invalid = isinstance(provider_validation, dict) and provider_validation.get("valid") is False
        if not validation.valid or provider_reported_invalid or generated.answer.strip().startswith("当前资料无法确认"):
            return AnswerResponse(
                status="refused",
                answer=REFUSAL_ANSWER,
                references=[],
                refusal_reason=validation.reason or "citation_validation_failed",
                trace_id=str(ctx.request_id),
                hit_summary=_hit_summary(result),
                latencies_ms=result.latencies_ms,
                metadata=metadata,
            )

        await self._update_query_log_answer(ctx, generated.answer)
        return AnswerResponse(
            status="success",
            answer=generated.answer,
            references=_references(result),
            hit_summary=_hit_summary(result),
            trace_id=str(ctx.request_id),
            latencies_ms=result.latencies_ms,
            metadata=metadata,
        )

    async def _retrieve_result(self, request: Any, ctx: RequestContext) -> tuple[RetrievalResult, list[str]]:
        try:
            filters = validate_client_filters(request.filters)
        except ValueError as exc:
            message = str(exc)
            code = "forbidden_filter" if "forbidden" in message else "invalid_filter"
            raise ValidationApiError(code, message) from exc
        warnings: list[str] = []
        if "date_range" in filters:
            warnings.append("date_range_filter_ignored")
        filters = await self._resolve_retrieval_scope(filters, ctx)
        self._validate_top_k(request)
        retrieval_request = RetrievalRequest(
            question=request.question,
            client_filters=filters,
            top_k=getattr(request, "top_k", None),
            final_top_k=getattr(request, "final_top_k", None),
            search_mode=getattr(request, "search_mode", "vector"),
        )
        async with self._retrieval_service() as retrieval_service:
            result = await retrieval_service.retrieve(retrieval_request, ctx)
        return result, warnings

    async def _resolve_upload_knowledge_base(self, command: DocumentUploadCommand, ctx: RequestContext):
        if self._session_factory is None:
            return None
        async with self._session_factory() as session:
            async with session.begin():
                kb_repo = KnowledgeBaseRepository(session)
                member_repo = KnowledgeBaseMemberRepository(session)
                permission = KnowledgeBasePermissionService(
                    member_repo,
                    max_kbs_per_query=self._settings.max_knowledge_bases_per_query,
                    allow_implicit_all=self._settings.allow_implicit_all_accessible_kbs,
                )
                audit = AuditLogger(AuditEventRepository(session), enabled=self._settings.audit_enabled)
                if command.knowledge_base_id is not None:
                    kb = await kb_repo.get(ctx.tenant_id, command.knowledge_base_id, statuses=("active",))
                    if kb is None:
                        raise ResourceNotFoundError("knowledge base not found")
                    try:
                        await permission.require_min_role(ctx, kb.id, "editor")
                    except KnowledgeBasePermissionError as exc:
                        await audit.write(
                            ctx,
                            action="document.upload",
                            resource_type="document",
                            outcome="denied",
                            knowledge_base_id=kb.id,
                            metadata={"code": exc.code},
                        )
                        raise ApiError(exc.code, exc.message, 403) from exc
                    return kb

                accessible = await member_repo.accessible_kb_ids(
                    ctx.tenant_id,
                    user_id=ctx.user_id,
                    department=ctx.department,
                    min_role="editor",
                    limit=1,
                )
                if accessible:
                    kb = await kb_repo.get(ctx.tenant_id, accessible[0], statuses=("active",))
                    return kb
                if not self._settings.require_knowledge_base_scope:
                    return None
                return await kb_repo.create(
                    KnowledgeBaseCreate(
                        tenant_id=ctx.tenant_id,
                        name=self._settings.default_knowledge_base_name,
                        owner_user_id=ctx.user_id,
                        created_by=ctx.user_id,
                        default_department=ctx.department,
                        default_access_level=ctx.access_level,
                    )
                )

    async def _resolve_retrieval_scope(self, filters: dict[str, Any], ctx: RequestContext) -> dict[str, Any]:
        requested = _extract_requested_kbs(filters)
        if self._session_factory is None:
            return filters
        async with self._session_factory() as session:
            async with session.begin():
                permission = KnowledgeBasePermissionService(
                    KnowledgeBaseMemberRepository(session),
                    max_kbs_per_query=self._settings.max_knowledge_bases_per_query,
                    allow_implicit_all=self._settings.allow_implicit_all_accessible_kbs,
                )
                audit = AuditLogger(AuditEventRepository(session), enabled=self._settings.audit_enabled)
                try:
                    scope = await permission.validate_retrieval_scope(ctx, requested)
                except KnowledgeBasePermissionError as exc:
                    await audit.write(
                        ctx,
                        action="retrieval.forbidden_kb",
                        resource_type="retrieval",
                        outcome="denied",
                        metadata={"code": exc.code, "requested_knowledge_base_ids": requested or []},
                    )
                    raise ApiError(exc.code, exc.message, 403) from exc
                resolved = dict(filters)
                resolved.pop("knowledge_base_id", None)
                resolved["knowledge_base_ids"] = scope.effective_ids
                await audit.write(
                    ctx,
                    action="retrieval.scope_resolved",
                    resource_type="retrieval",
                    knowledge_base_id=scope.effective_ids[0] if len(scope.effective_ids) == 1 else None,
                    metadata={
                        "requested_knowledge_base_ids": scope.requested_ids,
                        "effective_knowledge_base_ids": scope.effective_ids,
                    },
                )
                return resolved

    async def _maybe_backfill(self, document_id: int | None, ctx: RequestContext) -> tuple[str, dict[str, Any]]:
        if not self._settings.auto_embedding_backfill_on_ingest:
            return "not_requested", {}
        if document_id is None:
            return "skipped", {"reason": "missing_document_id"}
        if self._backfill_service is None or self._session_factory is None:
            return "not_configured", {}
        limit = min(self._settings.ingest_backfill_limit, self._settings.ingest_max_child_chunks_per_document)
        try:
            async with self._session_factory() as session:
                chunk_ids = await self._chunk_repo_type(session).list_ids_by_document(
                    ctx.tenant_id,
                    document_id,
                    limit=limit,
                )
            if not chunk_ids:
                return "skipped", {"reason": "no_active_chunks"}
            result = await self._backfill_service.backfill(
                BackfillRequest(
                    embedding_model=self._settings.embedding_model,
                    tenant_id=ctx.tenant_id,
                    chunk_ids=chunk_ids,
                    limit=len(chunk_ids),
                )
            )
            status = "succeeded" if result.failed == 0 else "failed"
            return status, asdict(result)
        except Exception as exc:
            return "failed", {"error_type": type(exc).__name__, "message": str(exc)[:300]}

    async def _update_query_log_answer(self, ctx: RequestContext, answer: str) -> None:
        if self._session_factory is None:
            raise ServiceUnavailableError("query_log_unavailable", "query log repository is not configured")
        async with self._session_factory() as session:
            async with session.begin():
                await self._query_log_repo_type(session).update_answer(ctx.request_id, ctx.tenant_id, answer)

    def _validate_top_k(self, request: Any) -> None:
        top_k = getattr(request, "top_k", None)
        final_top_k = getattr(request, "final_top_k", None)
        if top_k is not None and top_k > self._settings.default_top_k:
            raise ValidationApiError("top_k_too_large", "top_k exceeds the configured default_top_k")
        if final_top_k is not None and final_top_k > self._settings.final_top_k:
            raise ValidationApiError("final_top_k_too_large", "final_top_k exceeds the configured final_top_k")

    @asynccontextmanager
    async def _retrieval_service(self) -> AsyncIterator[Any]:
        candidate = self._retrieval_service_provider()
        if inspect.isawaitable(candidate):
            candidate = await candidate
        if hasattr(candidate, "__aenter__"):
            async with candidate as service:
                yield service
            return
        try:
            yield candidate
        finally:
            close = getattr(candidate, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result


def _references(result: RetrievalResult) -> list[ReferenceResponse]:
    responses = []
    for ref in result.references:
        data = asdict(ref)
        data["updated_at"] = None
        responses.append(ReferenceResponse(**data))
    return responses


def _hit_summary(result: RetrievalResult) -> list[HitSummaryResponse]:
    return [HitSummaryResponse(**asdict(item)) for item in result.hit_summary]


def _job_response(job: Any) -> IngestJobResponse:
    return IngestJobResponse(
        job_id=job.job_id,
        document_id=job.document_id,
        status=job.status,
        source_uri=job.source_uri,
        source_name=job.source_name,
        doc_type=job.doc_type,
        parser_used=job.parser_used,
        chunker_used=job.chunker_used,
        parent_chunk_count=job.parent_chunk_count,
        child_chunk_count=job.child_chunk_count,
        warnings=job.warnings,
        parse_report=job.parse_report,
        error_message=job.error_message,
        metadata=job.metadata,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _merge_metadata(metadata: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    merged = dict(metadata)
    if warnings:
        current = list(merged.get("warnings", []))
        merged["warnings"] = [*current, *warnings]
    return merged


def _extract_requested_kbs(filters: dict[str, Any]) -> list[int] | None:
    if "knowledge_base_id" in filters:
        return [int(filters["knowledge_base_id"])]
    if "knowledge_base_ids" in filters:
        return [int(item) for item in filters["knowledge_base_ids"]]
    return None


def _cleanup_upload(path: Any) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        return
    except OSError:
        return
