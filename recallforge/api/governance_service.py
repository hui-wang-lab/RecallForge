"""M6 knowledge-base governance orchestration."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from recallforge.config import Settings
from recallforge.context import RequestContext
from recallforge.governance.audit import AuditLogger
from recallforge.governance.permissions import KnowledgeBasePermissionError, KnowledgeBasePermissionService
from recallforge.storage.repository import (
    AuditEventRepository,
    ChunkRepository,
    DocumentRepository,
    DocumentVersionRepository,
    IngestJobRepository,
    KnowledgeBaseCreate,
    KnowledgeBaseMemberRepository,
    KnowledgeBaseRepository,
    KnowledgeBaseUpdate,
    ParentChunkRepository,
)

from .errors import ApiError, ResourceNotFoundError, ValidationApiError
from .knowledge_service import KnowledgeService
from .schemas import (
    ChildChunkDetailResponse,
    DocumentChunkDetailResponse,
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
    ParentChunkDetailResponse,
    ReindexRequest,
    ReindexResponse,
)


class AsyncSessionContext(Protocol):
    async def __aenter__(self) -> AsyncSession: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> object: ...


SessionFactory = Callable[[], AsyncSessionContext]
VectorStoreProvider = Callable[[AsyncSession], Any]


class GovernanceService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: SessionFactory,
        knowledge_service: KnowledgeService,
        vector_store_provider: VectorStoreProvider,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._knowledge_service = knowledge_service
        self._vector_store_provider = vector_store_provider

    async def create_knowledge_base(
        self,
        command: KnowledgeBaseCreateRequest,
        ctx: RequestContext,
    ) -> KnowledgeBaseResponse:
        async with self._session_factory() as session:
            async with session.begin():
                repo = KnowledgeBaseRepository(session)
                kb = await repo.create(
                    KnowledgeBaseCreate(
                        tenant_id=ctx.tenant_id,
                        name=command.name,
                        owner_user_id=ctx.user_id,
                        created_by=ctx.user_id,
                        description=command.description,
                        default_department=command.default_department,
                        default_access_level=command.default_access_level,
                        default_doc_type=command.default_doc_type,
                        default_parser=command.default_parser,
                        default_template=command.default_template,
                        default_search_mode=command.default_search_mode,
                        default_top_k=command.default_top_k,
                        default_final_top_k=command.default_final_top_k,
                        tags=command.tags,
                        metadata=command.metadata,
                    )
                )
                await self._audit(session).write(
                    ctx,
                    action="kb.create",
                    resource_type="knowledge_base",
                    knowledge_base_id=kb.id,
                    resource_id=str(kb.id),
                )
                actions = await self._permissions(session).allowed_actions(ctx, kb.id)
        return _kb_response(kb, ctx, actions)

    async def list_knowledge_bases(
        self,
        ctx: RequestContext,
        *,
        status: str = "active",
        q: str | None = None,
        tag: str | None = None,
        limit: int | None = None,
    ) -> KnowledgeBaseListResponse:
        async with self._session_factory() as session:
            repo = KnowledgeBaseRepository(session)
            items = await repo.list_visible(
                ctx.tenant_id,
                user_id=ctx.user_id,
                department=ctx.department,
                status=status,
                q=q,
                tag=tag,
                limit=limit or self._settings.kb_list_default_limit,
            )
            permission = self._permissions(session)
            responses = [_kb_response(item, ctx, await permission.allowed_actions(ctx, item.id)) for item in items]
        return KnowledgeBaseListResponse(items=responses, trace_id=str(ctx.request_id))

    async def get_knowledge_base(self, kb_id: int, ctx: RequestContext) -> KnowledgeBaseResponse:
        async with self._session_factory() as session:
            kb = await self._load_kb_for_action(session, ctx, kb_id, "view")
            actions = await self._permissions(session).allowed_actions(ctx, kb.id)
        return _kb_response(kb, ctx, actions)

    async def update_knowledge_base(
        self,
        kb_id: int,
        command: KnowledgeBaseUpdateRequest,
        ctx: RequestContext,
    ) -> KnowledgeBaseResponse:
        async with self._session_factory() as session:
            async with session.begin():
                await self._require(session, ctx, kb_id, "update_kb")
                kb = await KnowledgeBaseRepository(session).update(
                    ctx.tenant_id,
                    kb_id,
                    KnowledgeBaseUpdate(
                        name=command.name,
                        description=command.description,
                        default_department=command.default_department,
                        default_access_level=command.default_access_level,
                        default_doc_type=command.default_doc_type,
                        default_parser=command.default_parser,
                        default_template=command.default_template,
                        default_search_mode=command.default_search_mode,
                        default_top_k=command.default_top_k,
                        default_final_top_k=command.default_final_top_k,
                        tags=command.tags,
                        metadata=command.metadata,
                        updated_by=ctx.user_id,
                    ),
                )
                if kb is None:
                    raise ResourceNotFoundError("knowledge base not found")
                await self._audit(session).write(
                    ctx,
                    action="kb.update",
                    resource_type="knowledge_base",
                    knowledge_base_id=kb.id,
                    resource_id=str(kb.id),
                )
                actions = await self._permissions(session).allowed_actions(ctx, kb.id)
        return _kb_response(kb, ctx, actions)

    async def delete_knowledge_base(
        self,
        kb_id: int,
        command: KnowledgeBaseDeleteRequest,
        ctx: RequestContext,
    ) -> KnowledgeBaseResponse:
        async with self._session_factory() as session:
            async with session.begin():
                await self._require(session, ctx, kb_id, "delete_kb")
                status = "archived" if command.mode == "archive" else "deleted"
                kb = await KnowledgeBaseRepository(session).mark_status(ctx.tenant_id, kb_id, status, ctx.user_id)
                if kb is None:
                    raise ResourceNotFoundError("knowledge base not found")
                if command.mode == "delete":
                    docs = await DocumentRepository(session).list_by_knowledge_base(
                        ctx.tenant_id,
                        kb_id,
                        status="active",
                        limit=self._settings.reindex_max_documents_per_request,
                    )
                    vector_store = await self._vector_store(session)
                    for doc in docs:
                        await DocumentVersionRepository(session).delete_document_tree(
                            doc.id,
                            ctx.tenant_id,
                            ctx.user_id,
                            kb_id,
                        )
                        await vector_store.delete_by_document_id(doc.id, ctx.tenant_id)
                await self._audit(session).write(
                    ctx,
                    action="kb.archive" if command.mode == "archive" else "kb.delete",
                    resource_type="knowledge_base",
                    knowledge_base_id=kb.id,
                    resource_id=str(kb.id),
                    metadata={"reason": command.reason},
                )
                actions = await self._permissions(session).allowed_actions(ctx, kb.id)
        return _kb_response(kb, ctx, actions)

    async def upload_document(
        self,
        kb_id: int,
        command: DocumentUploadCommand,
        ctx: RequestContext,
    ) -> DocumentIngestResponse:
        async with self._session_factory() as session:
            async with session.begin():
                await self._require(session, ctx, kb_id, "upload")
        command.knowledge_base_id = kb_id
        response = await self._knowledge_service.ingest_document(command, ctx)
        async with self._session_factory() as session:
            async with session.begin():
                await self._audit(session).write(
                    ctx,
                    action="document.upload",
                    resource_type="document",
                    knowledge_base_id=kb_id,
                    document_id=response.document_id,
                    job_id=response.job_id,
                    resource_id=str(response.document_id) if response.document_id else None,
                )
        return response

    async def list_documents(
        self,
        kb_id: int,
        ctx: RequestContext,
        *,
        status: str = "active",
        doc_type: str | None = None,
        q: str | None = None,
        limit: int | None = None,
    ) -> DocumentListResponse:
        async with self._session_factory() as session:
            await self._require(session, ctx, kb_id, "view")
            docs = await DocumentRepository(session).list_by_knowledge_base(
                ctx.tenant_id,
                kb_id,
                status=status,  # type: ignore[arg-type]
                doc_type=doc_type,
                q=q,
                limit=limit or self._settings.document_list_default_limit,
            )
            job_repo = IngestJobRepository(session)
            items = []
            for doc in docs:
                jobs = await job_repo.list_by_knowledge_base(ctx.tenant_id, kb_id, document_id=doc.id, limit=1)
                job = jobs[0] if jobs else None
                items.append(_document_summary(doc, job))
        return DocumentListResponse(items=items, trace_id=str(ctx.request_id))

    async def get_document(self, kb_id: int, document_id: int, ctx: RequestContext) -> DocumentSummaryResponse:
        async with self._session_factory() as session:
            await self._require(session, ctx, kb_id, "view")
            doc = await DocumentRepository(session).get(
                document_id,
                ctx.tenant_id,
                statuses=("active", "superseded", "deleted"),
                knowledge_base_id=kb_id,
            )
            if doc is None:
                raise ResourceNotFoundError("document not found")
            jobs = await IngestJobRepository(session).list_by_knowledge_base(
                ctx.tenant_id,
                kb_id,
                document_id=doc.id,
                limit=1,
            )
        return _document_summary(doc, jobs[0] if jobs else None)

    async def list_document_chunks(
        self,
        kb_id: int,
        document_id: int,
        ctx: RequestContext,
        *,
        parent_limit: int = 200,
        child_limit: int = 500,
    ) -> DocumentChunkDetailResponse:
        async with self._session_factory() as session:
            await self._require(session, ctx, kb_id, "view")
            doc = await DocumentRepository(session).get(document_id, ctx.tenant_id, knowledge_base_id=kb_id)
            if doc is None:
                raise ResourceNotFoundError("document not found")
            parents = await ParentChunkRepository(session).list_by_document(
                ctx.tenant_id,
                document_id,
                knowledge_base_id=kb_id,
                limit=parent_limit,
            )
            children = await ChunkRepository(session).list_by_document(
                ctx.tenant_id,
                document_id,
                knowledge_base_id=kb_id,
                limit=child_limit,
            )

        children_by_parent: dict[int, list[Any]] = {}
        for child in children:
            children_by_parent.setdefault(child.parent_id, []).append(child)

        return DocumentChunkDetailResponse(
            document_id=document_id,
            knowledge_base_id=kb_id,
            parent_chunk_count=len(parents),
            child_chunk_count=len(children),
            items=[
                ParentChunkDetailResponse(
                    parent_id=parent.id,
                    parent_key=parent.parent_key,
                    chunk_index=parent.chunk_index,
                    content=parent.content,
                    token_count=parent.token_count,
                    page_start=parent.page_start,
                    page_end=parent.page_end,
                    heading_path=parent.heading_path,
                    status=parent.status,
                    child_chunks=[
                        ChildChunkDetailResponse(
                            chunk_id=child.id,
                            chunk_key=child.chunk_key,
                            parent_id=child.parent_id,
                            parent_key=child.parent_key,
                            chunk_index=child.chunk_index,
                            content=child.content,
                            page_start=child.page_start,
                            page_end=child.page_end,
                            heading_path=child.heading_path,
                            embedding_model=child.embedding_model,
                            embedding_dim=child.embedding_dim,
                            status=child.status,
                        )
                        for child in children_by_parent.get(parent.id, [])
                    ],
                )
                for parent in parents
            ],
            trace_id=str(ctx.request_id),
        )

    async def update_document(
        self,
        kb_id: int,
        document_id: int,
        command: DocumentUpdateRequest,
        ctx: RequestContext,
    ) -> DocumentSummaryResponse:
        async with self._session_factory() as session:
            async with session.begin():
                await self._require(session, ctx, kb_id, "update_document")
                doc = await DocumentRepository(session).update_metadata(
                    document_id,
                    ctx.tenant_id,
                    kb_id,
                    title=command.title,
                    source_name=command.source_name,
                    doc_type=command.doc_type,
                    metadata=command.metadata,
                    department=command.department,
                    access_level=command.access_level,
                    updated_by=ctx.user_id,
                )
                if doc is None:
                    raise ResourceNotFoundError("document not found")
                await self._audit(session).write(
                    ctx,
                    action="document.update_metadata",
                    resource_type="document",
                    knowledge_base_id=kb_id,
                    document_id=document_id,
                    resource_id=str(document_id),
                )
                jobs = await IngestJobRepository(session).list_by_knowledge_base(
                    ctx.tenant_id,
                    kb_id,
                    document_id=doc.id,
                    limit=1,
                )
        return _document_summary(doc, jobs[0] if jobs else None)

    async def delete_document(self, kb_id: int, document_id: int, ctx: RequestContext) -> DocumentDeleteResponse:
        async with self._session_factory() as session:
            async with session.begin():
                await self._require(session, ctx, kb_id, "delete_document")
                doc = await DocumentRepository(session).get(document_id, ctx.tenant_id, knowledge_base_id=kb_id)
                if doc is None:
                    raise ResourceNotFoundError("document not found")
                await DocumentVersionRepository(session).delete_document_tree(
                    document_id,
                    ctx.tenant_id,
                    ctx.user_id,
                    kb_id,
                )
                vector_status = "succeeded"
                try:
                    await (await self._vector_store(session)).delete_by_document_id(document_id, ctx.tenant_id)
                except Exception as exc:
                    vector_status = "failed"
                    if self._settings.document_delete_vector_sync_required:
                        raise ApiError("vector_delete_failed", str(exc), 503) from exc
                await self._audit(session).write(
                    ctx,
                    action="document.delete",
                    resource_type="document",
                    knowledge_base_id=kb_id,
                    document_id=document_id,
                    resource_id=str(document_id),
                    metadata={"vector_delete_status": vector_status},
                )
        return DocumentDeleteResponse(
            document_id=document_id,
            knowledge_base_id=kb_id,
            status="deleted",
            vector_delete_status=vector_status,
            trace_id=str(ctx.request_id),
        )

    async def list_ingest_jobs(self, kb_id: int, ctx: RequestContext, *, limit: int = 50) -> list[IngestJobResponse]:
        async with self._session_factory() as session:
            await self._require(session, ctx, kb_id, "view")
            jobs = await IngestJobRepository(session).list_by_knowledge_base(ctx.tenant_id, kb_id, limit=limit)
        return [_job_response(job) for job in jobs]

    async def reindex_knowledge_base(self, kb_id: int, command: ReindexRequest, ctx: RequestContext) -> ReindexResponse:
        limit = command.limit or self._settings.reindex_max_documents_per_request
        if limit > self._settings.reindex_max_documents_per_request:
            raise ValidationApiError("reindex_limit_exceeded", "reindex request exceeds configured limit")
        async with self._session_factory() as session:
            async with session.begin():
                await self._require(session, ctx, kb_id, "reindex")
                docs = await DocumentRepository(session).list_by_knowledge_base(
                    ctx.tenant_id,
                    kb_id,
                    status="active",
                    limit=limit,
                )
                if command.document_ids:
                    wanted = set(command.document_ids)
                    docs = [doc for doc in docs if doc.id in wanted]
                await self._audit(session).write(
                    ctx,
                    action="kb.reindex_requested",
                    resource_type="job",
                    knowledge_base_id=kb_id,
                    metadata={
                        "dry_run": command.dry_run,
                        "document_ids": command.document_ids,
                        "reason": command.reason,
                    },
                )
        return ReindexResponse(
            knowledge_base_id=kb_id,
            dry_run=command.dry_run,
            estimated_documents=len(docs),
            status="dry_run" if command.dry_run else "queued",
            trace_id=str(ctx.request_id),
        )

    async def _load_kb_for_action(self, session: AsyncSession, ctx: RequestContext, kb_id: int, action: str):
        await self._require(session, ctx, kb_id, action)
        kb = await KnowledgeBaseRepository(session).get(ctx.tenant_id, kb_id, statuses=("active", "archived"))
        if kb is None:
            raise ResourceNotFoundError("knowledge base not found")
        return kb

    async def _require(self, session: AsyncSession, ctx: RequestContext, kb_id: int, action: str) -> str:
        min_role = {
            "view": "viewer",
            "upload": "editor",
            "update_document": "editor",
            "delete_document": "editor",
            "reindex": "editor",
            "update_kb": "admin",
            "delete_kb": "owner",
        }[action]
        try:
            return await self._permissions(session).require_min_role(ctx, kb_id, min_role)
        except KnowledgeBasePermissionError as exc:
            await self._audit(session).write(
                ctx,
                action=f"{action}.forbidden",
                resource_type="knowledge_base",
                outcome="denied",
                knowledge_base_id=kb_id,
                metadata={"code": exc.code},
            )
            if exc.code == "knowledge_base_not_found":
                raise ResourceNotFoundError("knowledge base not found") from exc
            raise ApiError(exc.code, exc.message, 403) from exc

    def _permissions(self, session: AsyncSession) -> KnowledgeBasePermissionService:
        return KnowledgeBasePermissionService(
            KnowledgeBaseMemberRepository(session),
            max_kbs_per_query=self._settings.max_knowledge_bases_per_query,
            allow_implicit_all=self._settings.allow_implicit_all_accessible_kbs,
        )

    def _audit(self, session: AsyncSession) -> AuditLogger:
        return AuditLogger(AuditEventRepository(session), enabled=self._settings.audit_enabled)

    async def _vector_store(self, session: AsyncSession):
        candidate = self._vector_store_provider(session)
        if inspect.isawaitable(candidate):
            return await candidate
        return candidate


@asynccontextmanager
async def maybe_context(value: Any) -> AsyncIterator[Any]:
    if hasattr(value, "__aenter__"):
        async with value as entered:
            yield entered
    else:
        yield value


def _kb_response(kb: Any, ctx: RequestContext, actions: dict[str, bool]) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(
        knowledge_base_id=kb.id,
        name=kb.name,
        description=kb.description,
        status=kb.status,
        role=kb.role,
        tags=kb.tags,
        default_department=kb.default_department,
        default_access_level=kb.default_access_level,
        default_doc_type=kb.default_doc_type,
        default_parser=kb.default_parser,
        default_template=kb.default_template,
        default_search_mode=kb.default_search_mode,
        default_top_k=kb.default_top_k,
        default_final_top_k=kb.default_final_top_k,
        document_count=kb.document_count,
        active_chunk_count=kb.active_chunk_count,
        last_ingest_status=kb.last_ingest_status,
        last_query_at=kb.last_query_at,
        actions=actions,
        metadata=kb.metadata,
        created_at=kb.created_at,
        updated_at=kb.updated_at,
        trace_id=str(ctx.request_id),
    )


def _document_summary(doc: Any, job: Any | None) -> DocumentSummaryResponse:
    return DocumentSummaryResponse(
        document_id=doc.id,
        knowledge_base_id=doc.knowledge_base_id,
        source_uri=doc.source_uri,
        source_name=doc.source_name,
        title=doc.title,
        doc_type=doc.doc_type,
        version=doc.version,
        status=doc.status,
        content_hash=doc.content_hash,
        department=doc.department,
        access_level=doc.access_level,
        parent_chunk_count=job.parent_chunk_count if job else 0,
        child_chunk_count=job.child_chunk_count if job else 0,
        embedding_status="complete" if job and job.child_chunk_count > 0 else "unknown",
        last_ingest_job_id=job.job_id if job else None,
        last_ingest_status=job.status if job else None,
        warning_count=len(job.warnings) if job else 0,
        created_by=doc.created_by,
        updated_by=doc.updated_by,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


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
