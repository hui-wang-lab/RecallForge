"""Async repository layer for RecallForge M1 data foundation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Mapping, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from recallforge.storage.models import (
    RagChunk,
    RagDocument,
    RagIngestJob,
    RagParentChunk,
    RagQueryLog,
)

# ── Shared type aliases ─────────────────────────────────────────

TenantId = str
DocumentId = int
ParentChunkId = int
ChunkId = int
JobId = uuid.UUID
RequestId = uuid.UUID
DocumentStatus = Literal["active", "superseded", "deleted"]

# ── Data records ────────────────────────────────────────────────


@dataclass
class DocumentRecord:
    id: DocumentId
    tenant_id: TenantId
    source_uri: str
    source_name: str | None
    doc_type: str
    title: str | None
    content_hash: str
    version: int
    status: DocumentStatus
    department: str
    access_level: str
    metadata: dict[str, Any]
    created_by: str | None
    updated_by: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass
class ParentChunkRecord:
    id: ParentChunkId
    tenant_id: TenantId
    document_id: DocumentId
    source_uri: str
    doc_type: str
    parent_key: str
    chunk_index: int
    content: str
    content_hash: str
    department: str
    access_level: str
    heading_path: list[str] | None
    page_start: int | None
    page_end: int | None
    token_count: int | None
    status: DocumentStatus
    version: int
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass
class ChildChunkRecord:
    id: ChunkId
    tenant_id: TenantId
    document_id: DocumentId
    parent_id: ParentChunkId
    chunk_key: str
    parent_key: str
    chunk_index: int
    content: str
    content_hash: str
    doc_type: str
    chunk_type: str
    template: str | None
    department: str
    access_level: str
    heading_path: list[str] | None
    page_start: int | None
    page_end: int | None
    source_uri: str
    version: int
    status: DocumentStatus
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    embedding_metadata: dict[str, Any]
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass
class ChildChunkEmbeddingSource:
    id: ChunkId
    tenant_id: TenantId
    document_id: DocumentId
    parent_id: ParentChunkId
    chunk_key: str
    parent_key: str
    content: str
    doc_type: str
    chunk_type: str
    template: str | None
    department: str
    access_level: str
    heading_path: list[str] | None
    page_start: int | None
    page_end: int | None
    source_uri: str
    version: int
    status: DocumentStatus


@dataclass
class IngestJobRecord:
    id: int
    job_id: JobId
    tenant_id: TenantId
    document_id: DocumentId | None
    source_uri: str
    source_name: str | None
    doc_type: str | None
    status: str
    content_hash: str | None
    version: int | None
    parser: str | None
    template: str | None
    parser_used: str | None
    chunker_used: str | None
    parent_chunk_count: int
    child_chunk_count: int
    warnings: list[Any]
    parse_report: dict[str, Any]
    error_message: str | None
    metadata: dict[str, Any]
    created_by: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass
class QueryLogRecord:
    id: int
    request_id: RequestId
    tenant_id: TenantId
    user_id: str
    department: str
    access_level: str
    question: str
    rewritten_query: str | None
    filters: dict[str, Any]
    client_filters: dict[str, Any]
    search_mode: str
    embedding_provider: str | None
    embedding_model: str | None
    embedding_dim: int | None
    reranker_provider: str | None
    reranker_model: str | None
    top_k: int | None
    final_top_k: int | None
    min_rerank_score: float | None
    min_top1_margin: float | None
    max_context_tokens: int | None
    hit_summary: list[Any]
    selected_references: list[Any]
    answer: str | None
    refusal_reason: str | None
    latencies_ms: dict[str, Any]
    metadata: dict[str, Any]
    status: str
    error_message: str | None
    created_at: datetime


@dataclass
class FullTextHit:
    chunk_id: ChunkId
    document_id: DocumentId
    parent_id: ParentChunkId
    rank: int
    score: float
    score_source: str = "full_text"


@dataclass
class SupersedeResult:
    document_count: int
    parent_chunk_count: int
    child_chunk_count: int


# ── Create input types ──────────────────────────────────────────


@dataclass
class DocumentCreate:
    tenant_id: TenantId
    source_uri: str
    source_name: str | None = None
    doc_type: str = "markdown"
    title: str | None = None
    content_hash: str = ""
    version: int = 1
    department: str = "global"
    access_level: str = "public"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_by: str | None = None
    updated_by: str | None = None


@dataclass
class ParentChunkCreate:
    tenant_id: TenantId
    source_uri: str
    doc_type: str
    parent_key: str
    chunk_index: int
    content: str
    content_hash: str
    department: str
    access_level: str
    heading_path: list[str] | None = None
    page_start: int | None = None
    page_end: int | None = None
    token_count: int | None = None
    version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChildChunkCreate:
    tenant_id: TenantId
    parent_id: ParentChunkId
    parent_key: str
    chunk_key: str
    chunk_index: int
    content: str
    content_hash: str
    doc_type: str
    department: str
    access_level: str
    source_uri: str
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    version: int = 1
    chunk_type: str = "child"
    template: str | None = None
    heading_path: list[str] | None = None
    page_start: int | None = None
    page_end: int | None = None
    embedding_metadata: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestJobCreate:
    tenant_id: TenantId
    source_uri: str
    source_name: str | None = None
    doc_type: str | None = None
    content_hash: str | None = None
    version: int | None = None
    parser: str | None = None
    template: str | None = None
    created_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestJobSuccess:
    document_id: DocumentId
    content_hash: str
    version: int
    parser_used: str | None = None
    chunker_used: str | None = None
    parent_chunk_count: int = 0
    child_chunk_count: int = 0
    warnings: list[Any] = field(default_factory=list)
    parse_report: dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestJobSkippedDuplicate:
    document_id: DocumentId
    content_hash: str
    version: int
    parser_used: str | None = None
    chunker_used: str | None = None
    parent_chunk_count: int = 0
    child_chunk_count: int = 0
    warnings: list[Any] = field(default_factory=list)
    parse_report: dict[str, Any] = field(default_factory=dict)
    metadata_patch: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryLogCreate:
    request_id: RequestId
    tenant_id: TenantId
    user_id: str
    department: str
    access_level: str
    question: str
    search_mode: str
    rewritten_query: str | None = None
    filters: dict[str, Any] = field(default_factory=dict)
    client_filters: dict[str, Any] = field(default_factory=dict)
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_dim: int | None = None
    reranker_provider: str | None = None
    reranker_model: str | None = None
    top_k: int | None = None
    final_top_k: int | None = None
    min_rerank_score: float | None = None
    min_top1_margin: float | None = None
    max_context_tokens: int | None = None
    hit_summary: list[Any] = field(default_factory=list)
    selected_references: list[Any] = field(default_factory=list)
    answer: str | None = None
    refusal_reason: str | None = None
    latencies_ms: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "failed"
    error_message: str | None = None


@dataclass
class ChunkFilters:
    tenant_id: TenantId
    department: str | None = None
    access_level: str | None = None
    doc_type: str | None = None
    status: DocumentStatus | None = "active"
    version: int | None = None
    source_uri: str | None = None


# ── Helpers ─────────────────────────────────────────────────────


def _doc_to_record(row: RagDocument) -> DocumentRecord:
    return DocumentRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        source_uri=row.source_uri,
        source_name=row.source_name,
        doc_type=row.doc_type,
        title=row.title,
        content_hash=row.content_hash,
        version=row.version,
        status=row.status,
        department=row.department,
        access_level=row.access_level,
        metadata=row.metadata_ if row.metadata_ else {},
        created_by=row.created_by,
        updated_by=row.updated_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _parent_to_record(row: RagParentChunk) -> ParentChunkRecord:
    return ParentChunkRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        document_id=row.document_id,
        source_uri=row.source_uri,
        doc_type=row.doc_type,
        parent_key=row.parent_key,
        chunk_index=row.chunk_index,
        content=row.content,
        content_hash=row.content_hash,
        department=row.department,
        access_level=row.access_level,
        heading_path=row.heading_path,
        page_start=row.page_start,
        page_end=row.page_end,
        token_count=row.token_count,
        status=row.status,
        version=row.version,
        metadata=row.metadata_ if row.metadata_ else {},
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _chunk_to_record(row: RagChunk) -> ChildChunkRecord:
    return ChildChunkRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        document_id=row.document_id,
        parent_id=row.parent_id,
        chunk_key=row.chunk_key,
        parent_key=row.parent_key,
        chunk_index=row.chunk_index,
        content=row.content,
        content_hash=row.content_hash,
        doc_type=row.doc_type,
        chunk_type=row.chunk_type,
        template=row.template,
        department=row.department,
        access_level=row.access_level,
        heading_path=row.heading_path,
        page_start=row.page_start,
        page_end=row.page_end,
        source_uri=row.source_uri,
        version=row.version,
        status=row.status,
        embedding_provider=row.embedding_provider,
        embedding_model=row.embedding_model,
        embedding_dim=row.embedding_dim,
        embedding_metadata=row.embedding_metadata if row.embedding_metadata else {},
        metadata=row.metadata_ if row.metadata_ else {},
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _job_to_record(row: RagIngestJob) -> IngestJobRecord:
    return IngestJobRecord(
        id=row.id,
        job_id=row.job_id,
        tenant_id=row.tenant_id,
        document_id=row.document_id,
        source_uri=row.source_uri,
        source_name=row.source_name,
        doc_type=row.doc_type,
        status=row.status,
        content_hash=row.content_hash,
        version=row.version,
        parser=row.parser,
        template=row.template,
        parser_used=row.parser_used,
        chunker_used=row.chunker_used,
        parent_chunk_count=row.parent_chunk_count,
        child_chunk_count=row.child_chunk_count,
        warnings=row.warnings if row.warnings else [],
        parse_report=row.parse_report if row.parse_report else {},
        error_message=row.error_message,
        metadata=row.metadata_ if row.metadata_ else {},
        created_by=row.created_by,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _query_log_to_record(row: RagQueryLog) -> QueryLogRecord:
    return QueryLogRecord(
        id=row.id,
        request_id=row.request_id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        department=row.department,
        access_level=row.access_level,
        question=row.question,
        rewritten_query=row.rewritten_query,
        filters=row.filters if row.filters else {},
        client_filters=row.client_filters if row.client_filters else {},
        search_mode=row.search_mode,
        embedding_provider=row.embedding_provider,
        embedding_model=row.embedding_model,
        embedding_dim=row.embedding_dim,
        reranker_provider=row.reranker_provider,
        reranker_model=row.reranker_model,
        top_k=row.top_k,
        final_top_k=row.final_top_k,
        min_rerank_score=float(row.min_rerank_score) if row.min_rerank_score is not None else None,
        min_top1_margin=float(row.min_top1_margin) if row.min_top1_margin is not None else None,
        max_context_tokens=row.max_context_tokens,
        hit_summary=row.hit_summary if row.hit_summary else [],
        selected_references=row.selected_references if row.selected_references else [],
        answer=row.answer,
        refusal_reason=row.refusal_reason,
        latencies_ms=row.latencies_ms if row.latencies_ms else {},
        metadata=row.metadata_ if row.metadata_ else {},
        status=row.status,
        error_message=row.error_message,
        created_at=row.created_at,
    )


# ── DocumentRepository ──────────────────────────────────────────


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, input: DocumentCreate) -> DocumentRecord:
        row = RagDocument(
            tenant_id=input.tenant_id,
            source_uri=input.source_uri,
            source_name=input.source_name,
            doc_type=input.doc_type,
            title=input.title,
            content_hash=input.content_hash,
            version=input.version,
            status="active",
            department=input.department,
            access_level=input.access_level,
            metadata_=input.metadata,
            created_by=input.created_by,
            updated_by=input.updated_by,
        )
        self._session.add(row)
        await self._session.flush()
        return _doc_to_record(row)

    async def get(
        self,
        document_id: DocumentId,
        tenant_id: TenantId,
        statuses: Sequence[DocumentStatus] = ("active",),
    ) -> DocumentRecord | None:
        stmt = select(RagDocument).where(
            RagDocument.id == document_id,
            RagDocument.tenant_id == tenant_id,
            RagDocument.status.in_(statuses),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _doc_to_record(row) if row else None

    async def get_latest_by_source(
        self,
        tenant_id: TenantId,
        source_uri: str,
        statuses: Sequence[DocumentStatus] = ("active",),
    ) -> DocumentRecord | None:
        stmt = (
            select(RagDocument)
            .where(
                RagDocument.tenant_id == tenant_id,
                RagDocument.source_uri == source_uri,
                RagDocument.status.in_(statuses),
            )
            .order_by(RagDocument.version.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _doc_to_record(row) if row else None

    async def get_by_ids(
        self,
        tenant_id: TenantId,
        document_ids: Sequence[DocumentId],
        statuses: Sequence[DocumentStatus] = ("active",),
    ) -> list[DocumentRecord]:
        if not document_ids:
            return []
        stmt = select(RagDocument).where(
            RagDocument.tenant_id == tenant_id,
            RagDocument.id.in_(document_ids),
            RagDocument.status.in_(statuses),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_doc_to_record(row) for row in rows]

    async def lock_active_by_source(
        self,
        tenant_id: TenantId,
        source_uri: str,
    ) -> DocumentRecord | None:
        stmt = (
            select(RagDocument)
            .where(
                RagDocument.tenant_id == tenant_id,
                RagDocument.source_uri == source_uri,
                RagDocument.status == "active",
            )
            .with_for_update()
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _doc_to_record(row) if row else None

    async def find_by_source_hash(
        self,
        tenant_id: TenantId,
        source_uri: str,
        content_hash: str,
        statuses: Sequence[DocumentStatus] = ("active", "superseded"),
    ) -> DocumentRecord | None:
        stmt = (
            select(RagDocument)
            .where(
                RagDocument.tenant_id == tenant_id,
                RagDocument.source_uri == source_uri,
                RagDocument.content_hash == content_hash,
                RagDocument.status.in_(statuses),
            )
            .order_by(RagDocument.version.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _doc_to_record(row) if row else None

    async def next_version(self, tenant_id: TenantId, source_uri: str) -> int:
        stmt = select(func.max(RagDocument.version)).where(
            RagDocument.tenant_id == tenant_id,
            RagDocument.source_uri == source_uri,
        )
        result = (await self._session.execute(stmt)).scalar()
        return (result or 0) + 1

    async def mark_deleted(
        self,
        document_id: DocumentId,
        tenant_id: TenantId,
        deleted_by: str | None = None,
    ) -> DocumentRecord:
        now = datetime.now(UTC)
        stmt = (
            update(RagDocument)
            .where(
                RagDocument.id == document_id,
                RagDocument.tenant_id == tenant_id,
                RagDocument.status != "deleted",
            )
            .values(status="deleted", deleted_at=now, updated_at=now, updated_by=deleted_by)
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            row = await self._session.get(RagDocument, document_id)
            if row is None or row.tenant_id != tenant_id:
                msg = f"Document {document_id} not found for tenant {tenant_id}"
                raise ValueError(msg)
            return _doc_to_record(row)

        # Cascade deletion to parent and child chunks per AGENTS.md requirement.
        parent_stmt = (
            update(RagParentChunk)
            .where(
                RagParentChunk.document_id == document_id,
                RagParentChunk.tenant_id == tenant_id,
                RagParentChunk.status != "deleted",
            )
            .values(status="deleted", deleted_at=now, updated_at=now)
        )
        await self._session.execute(parent_stmt)

        child_stmt = (
            update(RagChunk)
            .where(
                RagChunk.document_id == document_id,
                RagChunk.tenant_id == tenant_id,
                RagChunk.status != "deleted",
            )
            .values(status="deleted", deleted_at=now, updated_at=now)
        )
        await self._session.execute(child_stmt)

        row = await self._session.get(RagDocument, document_id)
        return _doc_to_record(row)


# ── DocumentVersionRepository ───────────────────────────────────


class DocumentVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def supersede_source(
        self,
        tenant_id: TenantId,
        source_uri: str,
    ) -> SupersedeResult:
        now = datetime.now(UTC)
        doc_stmt = (
            update(RagDocument)
            .where(
                RagDocument.tenant_id == tenant_id,
                RagDocument.source_uri == source_uri,
                RagDocument.status == "active",
            )
            .values(status="superseded", updated_at=now)
            .returning(RagDocument.id)
        )
        doc_result = (await self._session.execute(doc_stmt)).fetchall()
        doc_count = len(doc_result)
        doc_ids = [r[0] for r in doc_result]

        parent_count = 0
        child_count = 0
        if doc_ids:
            parent_stmt = (
                update(RagParentChunk)
                .where(
                    RagParentChunk.tenant_id == tenant_id,
                    RagParentChunk.document_id.in_(doc_ids),
                    RagParentChunk.status == "active",
                )
                .values(status="superseded", updated_at=now)
            )
            parent_result = await self._session.execute(parent_stmt)
            parent_count = parent_result.rowcount  # type: ignore[assignment]

            child_stmt = (
                update(RagChunk)
                .where(
                    RagChunk.tenant_id == tenant_id,
                    RagChunk.document_id.in_(doc_ids),
                    RagChunk.status == "active",
                )
                .values(status="superseded", updated_at=now)
            )
            child_result = await self._session.execute(child_stmt)
            child_count = child_result.rowcount  # type: ignore[assignment]

        return SupersedeResult(
            document_count=doc_count,
            parent_chunk_count=parent_count,
            child_chunk_count=child_count,
        )

    async def delete_document_tree(
        self,
        document_id: DocumentId,
        tenant_id: TenantId,
        deleted_by: str | None = None,
    ) -> SupersedeResult:
        now = datetime.now(UTC)
        doc_stmt = (
            update(RagDocument)
            .where(
                RagDocument.id == document_id,
                RagDocument.tenant_id == tenant_id,
                RagDocument.status != "deleted",
            )
            .values(status="deleted", deleted_at=now, updated_at=now, updated_by=deleted_by)
            .returning(RagDocument.id)
        )
        doc_result = (await self._session.execute(doc_stmt)).fetchall()
        doc_count = len(doc_result)

        parent_stmt = (
            update(RagParentChunk)
            .where(
                RagParentChunk.document_id == document_id,
                RagParentChunk.tenant_id == tenant_id,
                RagParentChunk.status != "deleted",
            )
            .values(status="deleted", deleted_at=now, updated_at=now)
        )
        parent_result = await self._session.execute(parent_stmt)
        parent_count = parent_result.rowcount  # type: ignore[assignment]

        child_stmt = (
            update(RagChunk)
            .where(
                RagChunk.document_id == document_id,
                RagChunk.tenant_id == tenant_id,
                RagChunk.status != "deleted",
            )
            .values(status="deleted", deleted_at=now, updated_at=now)
        )
        child_result = await self._session.execute(child_stmt)
        child_count = child_result.rowcount  # type: ignore[assignment]

        return SupersedeResult(
            document_count=doc_count,
            parent_chunk_count=parent_count,
            child_chunk_count=child_count,
        )

    async def restore_version(
        self,
        source_document_id: DocumentId,
        tenant_id: TenantId,
        restored_by: str | None = None,
    ) -> DocumentRecord:
        source = await self._session.get(RagDocument, source_document_id)
        if source is None or source.tenant_id != tenant_id:
            msg = f"Document {source_document_id} not found for tenant {tenant_id}"
            raise ValueError(msg)

        doc_repo = DocumentRepository(self._session)
        new_version = await doc_repo.next_version(tenant_id, source.source_uri)

        # Supersede current active
        await self.supersede_source(tenant_id, source.source_uri)

        # Create new active document (clone of source)
        new_doc = RagDocument(
            tenant_id=source.tenant_id,
            source_uri=source.source_uri,
            source_name=source.source_name,
            doc_type=source.doc_type,
            title=source.title,
            content_hash=source.content_hash,
            version=new_version,
            status="active",
            department=source.department,
            access_level=source.access_level,
            metadata_=source.metadata_,
            created_by=restored_by,
            updated_by=restored_by,
        )
        self._session.add(new_doc)
        await self._session.flush()

        # Clone parent chunks — use parent_key mapping to avoid relying on row ordering
        parent_stmt = (
            select(RagParentChunk)
            .where(
                RagParentChunk.document_id == source_document_id,
                RagParentChunk.tenant_id == tenant_id,
            )
            .order_by(RagParentChunk.id)
        )
        parents = (await self._session.execute(parent_stmt)).scalars().all()

        old_to_new_parent: dict[int, int] = {}
        for p in parents:
            new_p = RagParentChunk(
                tenant_id=p.tenant_id,
                document_id=new_doc.id,
                source_uri=p.source_uri,
                doc_type=p.doc_type,
                parent_key=p.parent_key,
                chunk_index=p.chunk_index,
                content=p.content,
                content_hash=p.content_hash,
                department=p.department,
                access_level=p.access_level,
                heading_path=p.heading_path,
                page_start=p.page_start,
                page_end=p.page_end,
                token_count=p.token_count,
                status="active",
                version=new_version,
                metadata_=p.metadata_,
            )
            self._session.add(new_p)
            await self._session.flush()
            old_to_new_parent[p.id] = new_p.id

        # Clone child chunks — carry over embedding vectors and metadata so that
        # restored versions remain immediately searchable without M3 re-backfill.
        chunk_stmt = (
            select(RagChunk)
            .where(
                RagChunk.document_id == source_document_id,
                RagChunk.tenant_id == tenant_id,
            )
            .order_by(RagChunk.id)
        )
        chunks = (await self._session.execute(chunk_stmt)).scalars().all()
        from recallforge.storage.embedding_columns import DEFAULT_EMBEDDING_COLUMNS

        embedding_columns = DEFAULT_EMBEDDING_COLUMNS
        for c in chunks:
            new_parent_id = old_to_new_parent.get(c.parent_id, c.parent_id)
            new_c = RagChunk(
                tenant_id=c.tenant_id,
                document_id=new_doc.id,
                parent_id=new_parent_id,
                chunk_key=c.chunk_key,
                parent_key=c.parent_key,
                chunk_index=c.chunk_index,
                content=c.content,
                content_hash=c.content_hash,
                doc_type=c.doc_type,
                chunk_type=c.chunk_type,
                template=c.template,
                department=c.department,
                access_level=c.access_level,
                heading_path=c.heading_path,
                page_start=c.page_start,
                page_end=c.page_end,
                source_uri=c.source_uri,
                version=new_version,
                status="active",
                embedding_provider=c.embedding_provider,
                embedding_model=c.embedding_model,
                embedding_dim=c.embedding_dim,
                embedding_metadata=c.embedding_metadata if c.embedding_metadata else {},
                metadata_=c.metadata_,
            )
            # Dynamically copy all registered vector columns so that newly
            # added embedding models are preserved across version restores.
            for spec in embedding_columns.all_specs():
                setattr(new_c, spec.column_name, getattr(c, spec.column_name))
            self._session.add(new_c)

        await self._session.flush()
        return _doc_to_record(new_doc)


# ── ParentChunkRepository ───────────────────────────────────────


class ParentChunkRepository:
    # Default batch size for bulk inserts to avoid large transaction issues
    BULK_BATCH_SIZE = 1000

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_create(
        self,
        document_id: DocumentId,
        chunks: Sequence[ParentChunkCreate],
        *,
        batch_size: int | None = None,
    ) -> list[ParentChunkRecord]:
        if not chunks:
            return []

        batch_size = batch_size or self.BULK_BATCH_SIZE
        all_records: list[ParentChunkRecord] = []

        # Process in batches to avoid memory issues with large documents
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            rows = await self._bulk_insert_batch(document_id, batch)
            all_records.extend([_parent_to_record(r) for r in rows])

        return all_records

    async def _bulk_insert_batch(
        self,
        document_id: DocumentId,
        chunks: Sequence[ParentChunkCreate],
    ) -> list[RagParentChunk]:
        """Insert a batch of parent chunks and return the inserted rows."""
        from sqlalchemy import insert

        # Build mapping dictionaries for each chunk
        mappings = []
        for c in chunks:
            mappings.append({
                "tenant_id": c.tenant_id,
                "document_id": document_id,
                "source_uri": c.source_uri,
                "doc_type": c.doc_type,
                "parent_key": c.parent_key,
                "chunk_index": c.chunk_index,
                "content": c.content,
                "content_hash": c.content_hash,
                "department": c.department,
                "access_level": c.access_level,
                "heading_path": c.heading_path,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "token_count": c.token_count,
                "status": "active",
                "version": c.version,
                "metadata_": c.metadata,
            })

        # Use insert().returning() for efficient bulk insert with ID retrieval
        stmt = insert(RagParentChunk).returning(RagParentChunk)
        result = await self._session.execute(stmt, mappings)
        return list(result.scalars().all())

    async def get(
        self,
        parent_id: ParentChunkId,
        tenant_id: TenantId,
        statuses: Sequence[DocumentStatus] = ("active",),
    ) -> ParentChunkRecord | None:
        stmt = select(RagParentChunk).where(
            RagParentChunk.id == parent_id,
            RagParentChunk.tenant_id == tenant_id,
            RagParentChunk.status.in_(statuses),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _parent_to_record(row) if row else None

    async def get_by_document_and_key(
        self,
        document_id: DocumentId,
        parent_key: str,
        statuses: Sequence[DocumentStatus] = ("active",),
    ) -> ParentChunkRecord | None:
        stmt = select(RagParentChunk).where(
            RagParentChunk.document_id == document_id,
            RagParentChunk.parent_key == parent_key,
            RagParentChunk.status.in_(statuses),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _parent_to_record(row) if row else None

    async def get_by_ids(
        self,
        tenant_id: TenantId,
        parent_ids: Sequence[ParentChunkId],
        statuses: Sequence[DocumentStatus] = ("active",),
    ) -> list[ParentChunkRecord]:
        if not parent_ids:
            return []
        stmt = select(RagParentChunk).where(
            RagParentChunk.tenant_id == tenant_id,
            RagParentChunk.id.in_(parent_ids),
            RagParentChunk.status.in_(statuses),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_parent_to_record(r) for r in rows]

    async def mark_by_document_status(
        self,
        document_id: DocumentId,
        tenant_id: TenantId,
        status: DocumentStatus,
    ) -> int:
        now = datetime.now(UTC)
        stmt = (
            update(RagParentChunk)
            .where(
                RagParentChunk.document_id == document_id,
                RagParentChunk.tenant_id == tenant_id,
                RagParentChunk.status != status,
            )
            .values(status=status, updated_at=now, deleted_at=now if status == "deleted" else None)
        )
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[return-value]


# ── ChunkRepository ─────────────────────────────────────────────


class ChunkRepository:
    # Default batch size for bulk inserts to avoid large transaction issues
    BULK_BATCH_SIZE = 1000

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_create(
        self,
        document_id: DocumentId,
        chunks: Sequence[ChildChunkCreate],
        *,
        batch_size: int | None = None,
    ) -> list[ChildChunkRecord]:
        if not chunks:
            return []

        batch_size = batch_size or self.BULK_BATCH_SIZE
        all_records: list[ChildChunkRecord] = []

        # Process in batches to avoid memory issues with large documents
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            rows = await self._bulk_insert_batch(document_id, batch)
            all_records.extend([_chunk_to_record(r) for r in rows])

        return all_records

    async def _bulk_insert_batch(
        self,
        document_id: DocumentId,
        chunks: Sequence[ChildChunkCreate],
    ) -> list[RagChunk]:
        """Insert a batch of child chunks and return the inserted rows."""
        from sqlalchemy import insert

        # Build mapping dictionaries for each chunk
        mappings = []
        for c in chunks:
            mappings.append({
                "tenant_id": c.tenant_id,
                "document_id": document_id,
                "parent_id": c.parent_id,
                "parent_key": c.parent_key,
                "chunk_key": c.chunk_key,
                "chunk_index": c.chunk_index,
                "content": c.content,
                "content_hash": c.content_hash,
                "doc_type": c.doc_type,
                "chunk_type": c.chunk_type,
                "template": c.template,
                "department": c.department,
                "access_level": c.access_level,
                "heading_path": c.heading_path,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "source_uri": c.source_uri,
                "version": c.version,
                "status": "active",
                "embedding_provider": c.embedding_provider,
                "embedding_model": c.embedding_model,
                "embedding_dim": c.embedding_dim,
                "embedding_metadata": c.embedding_metadata,
                "metadata_": c.metadata,
            })

        # Use insert().returning() for efficient bulk insert with ID retrieval
        stmt = insert(RagChunk).returning(RagChunk)
        result = await self._session.execute(stmt, mappings)
        return list(result.scalars().all())

    async def get(
        self,
        chunk_id: ChunkId,
        tenant_id: TenantId,
        statuses: Sequence[DocumentStatus] = ("active",),
    ) -> ChildChunkRecord | None:
        stmt = select(RagChunk).where(
            RagChunk.id == chunk_id,
            RagChunk.tenant_id == tenant_id,
            RagChunk.status.in_(statuses),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _chunk_to_record(row) if row else None

    async def get_by_ids(
        self,
        tenant_id: TenantId,
        chunk_ids: Sequence[ChunkId],
        statuses: Sequence[DocumentStatus] = ("active",),
    ) -> list[ChildChunkRecord]:
        if not chunk_ids:
            return []
        stmt = select(RagChunk).where(
            RagChunk.tenant_id == tenant_id,
            RagChunk.id.in_(chunk_ids),
            RagChunk.status.in_(statuses),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_chunk_to_record(r) for r in rows]

    async def get_by_parent_id(
        self,
        tenant_id: TenantId,
        parent_id: ParentChunkId,
        statuses: Sequence[DocumentStatus] = ("active",),
    ) -> list[ChildChunkRecord]:
        stmt = select(RagChunk).where(
            RagChunk.tenant_id == tenant_id,
            RagChunk.parent_id == parent_id,
            RagChunk.status.in_(statuses),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_chunk_to_record(r) for r in rows]

    async def list_for_embedding_backfill(
        self,
        embedding_model: str,
        limit: int,
        tenant_id: TenantId | None = None,
        statuses: Sequence[DocumentStatus] = ("active",),
        *,
        columns: Any | None = None,
        chunk_ids: Sequence[ChunkId] | None = None,
        force: bool = False,
    ) -> list[ChildChunkEmbeddingSource]:
        if limit <= 0:
            return []
        if columns is None:
            from recallforge.storage.embedding_columns import DEFAULT_EMBEDDING_COLUMNS

            columns = DEFAULT_EMBEDDING_COLUMNS
        if chunk_ids is not None and not chunk_ids:
            return []
        spec = columns.resolve(embedding_model)
        vector_column = getattr(RagChunk, spec.column_name)

        stmt = select(RagChunk).where(RagChunk.status.in_(statuses)).limit(limit)
        if not force:
            stmt = stmt.where(vector_column.is_(None))
        if tenant_id is not None:
            stmt = stmt.where(RagChunk.tenant_id == tenant_id)
        if chunk_ids is not None:
            stmt = stmt.where(RagChunk.id.in_(chunk_ids))
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            ChildChunkEmbeddingSource(
                id=r.id,
                tenant_id=r.tenant_id,
                document_id=r.document_id,
                parent_id=r.parent_id,
                chunk_key=r.chunk_key,
                parent_key=r.parent_key,
                content=r.content,
                doc_type=r.doc_type,
                chunk_type=r.chunk_type,
                template=r.template,
                department=r.department,
                access_level=r.access_level,
                heading_path=r.heading_path,
                page_start=r.page_start,
                page_end=r.page_end,
                source_uri=r.source_uri,
                version=r.version,
                status=r.status,
            )
            for r in rows
        ]

    async def mark_by_document_status(
        self,
        document_id: DocumentId,
        tenant_id: TenantId,
        status: DocumentStatus,
    ) -> int:
        now = datetime.now(UTC)
        stmt = (
            update(RagChunk)
            .where(
                RagChunk.document_id == document_id,
                RagChunk.tenant_id == tenant_id,
                RagChunk.status != status,
            )
            .values(status=status, updated_at=now, deleted_at=now if status == "deleted" else None)
        )
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[return-value]

    # TODO(M4): access_level filtering uses exact match here. The server-side filter
    # construction layer must expand access_level into an allowed set (e.g. a
    # confidential user should see public + internal + confidential), not pass a
    # single level. This must be fixed when implementing the retrieval pipeline.
    async def search_full_text(
        self,
        query: str,
        filters: ChunkFilters,
        limit: int,
    ) -> list[FullTextHit]:
        from sqlalchemy import text

        sql = text("""
            SELECT
                id AS chunk_id,
                document_id,
                parent_id,
                ts_rank_cd(content_tsv, plainto_tsquery('simple', :query)) AS score
            FROM rag_chunks
            WHERE tenant_id = :tenant_id
              AND status = :status
              AND content_tsv @@ plainto_tsquery('simple', :query)
              AND (:department IS NULL OR department = :department)
              AND (:access_level IS NULL OR access_level = :access_level)
              AND (:doc_type IS NULL OR doc_type = :doc_type)
              AND (:version IS NULL OR version = :version)
              AND (:source_uri IS NULL OR source_uri = :source_uri)
            ORDER BY score DESC
            LIMIT :limit
        """)
        result = await self._session.execute(
            sql,
            {
                "query": query,
                "tenant_id": filters.tenant_id,
                "status": filters.status if filters.status else "active",
                "department": filters.department,
                "access_level": filters.access_level,
                "doc_type": filters.doc_type,
                "version": filters.version,
                "source_uri": filters.source_uri,
                "limit": limit,
            },
        )
        hits = []
        for i, row in enumerate(result, start=1):
            hits.append(
                FullTextHit(
                    chunk_id=row.chunk_id,
                    document_id=row.document_id,
                    parent_id=row.parent_id,
                    rank=i,
                    score=row.score,
                    score_source="full_text",
                )
            )
        return hits


# ── IngestJobRepository ─────────────────────────────────────────


class IngestJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, input: IngestJobCreate) -> IngestJobRecord:
        row = RagIngestJob(
            job_id=uuid.uuid4(),
            tenant_id=input.tenant_id,
            source_uri=input.source_uri,
            source_name=input.source_name,
            doc_type=input.doc_type,
            status="pending",
            content_hash=input.content_hash,
            version=input.version,
            parser=input.parser,
            template=input.template,
            created_by=input.created_by,
            metadata_=input.metadata,
        )
        self._session.add(row)
        await self._session.flush()
        return _job_to_record(row)

    async def get(self, job_id: JobId, tenant_id: TenantId) -> IngestJobRecord | None:
        stmt = select(RagIngestJob).where(
            RagIngestJob.job_id == job_id,
            RagIngestJob.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _job_to_record(row) if row else None

    async def mark_running(self, job_id: JobId, tenant_id: TenantId) -> IngestJobRecord:
        now = datetime.now(UTC)
        stmt = (
            update(RagIngestJob)
            .where(
                RagIngestJob.job_id == job_id,
                RagIngestJob.tenant_id == tenant_id,
                RagIngestJob.status == "pending",
            )
            .values(status="running", started_at=now, updated_at=now)
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            # Idempotent: already running, return existing record
            row = (await self._session.execute(
                select(RagIngestJob).where(
                    RagIngestJob.job_id == job_id,
                    RagIngestJob.tenant_id == tenant_id,
                    RagIngestJob.status == "running",
                )
            )).scalar_one_or_none()
            if row is not None:
                return _job_to_record(row)
            msg = f"IngestJob {job_id} not found or not in pending/running state for tenant {tenant_id}"
            raise ValueError(msg)
        row = (await self._session.execute(
            select(RagIngestJob).where(
                RagIngestJob.job_id == job_id,
                RagIngestJob.tenant_id == tenant_id,
            )
        )).scalar_one()
        return _job_to_record(row)

    async def mark_success(
        self,
        job_id: JobId,
        tenant_id: TenantId,
        result: IngestJobSuccess,
    ) -> IngestJobRecord:
        now = datetime.now(UTC)
        stmt = (
            update(RagIngestJob)
            .where(
                RagIngestJob.job_id == job_id,
                RagIngestJob.tenant_id == tenant_id,
                RagIngestJob.status == "running",
            )
            .values(
                status="success",
                document_id=result.document_id,
                content_hash=result.content_hash,
                version=result.version,
                parser_used=result.parser_used,
                chunker_used=result.chunker_used,
                parent_chunk_count=result.parent_chunk_count,
                child_chunk_count=result.child_chunk_count,
                warnings=result.warnings,
                parse_report=result.parse_report,
                finished_at=now,
                updated_at=now,
            )
        )
        update_result = await self._session.execute(stmt)
        if update_result.rowcount == 0:
            msg = f"IngestJob {job_id} not in running state for tenant {tenant_id}"
            raise ValueError(msg)
        row = (await self._session.execute(
            select(RagIngestJob).where(
                RagIngestJob.job_id == job_id,
                RagIngestJob.tenant_id == tenant_id,
            )
        )).scalar_one()
        return _job_to_record(row)

    async def mark_failed(
        self,
        job_id: JobId,
        tenant_id: TenantId,
        error_message: str,
        diagnostics: Mapping[str, Any],
        *,
        warnings: Sequence[Any] | None = None,
        parse_report: Mapping[str, Any] | None = None,
    ) -> IngestJobRecord:
        now = datetime.now(UTC)
        # Merge diagnostics into existing metadata instead of overwriting
        existing = (await self._session.execute(
            select(RagIngestJob.metadata_).where(
                RagIngestJob.job_id == job_id,
                RagIngestJob.tenant_id == tenant_id,
                RagIngestJob.status == "running",
            )
        )).scalar_one_or_none()
        merged = {**(existing if existing else {}), **dict(diagnostics)}
        values: dict[str, Any] = {
            "status": "failed",
            "error_message": error_message,
            "metadata_": merged,
            "finished_at": now,
            "updated_at": now,
        }
        if warnings is not None:
            values["warnings"] = list(warnings)
        if parse_report is not None:
            values["parse_report"] = dict(parse_report)
        stmt = (
            update(RagIngestJob)
            .where(
                RagIngestJob.job_id == job_id,
                RagIngestJob.tenant_id == tenant_id,
                RagIngestJob.status == "running",
            )
            .values(**values)
        )
        update_result = await self._session.execute(stmt)
        if update_result.rowcount == 0:
            msg = f"IngestJob {job_id} not in running state for tenant {tenant_id}"
            raise ValueError(msg)
        row = (await self._session.execute(
            select(RagIngestJob).where(
                RagIngestJob.job_id == job_id,
                RagIngestJob.tenant_id == tenant_id,
            )
        )).scalar_one()
        return _job_to_record(row)

    async def mark_skipped_duplicate(
        self,
        job_id: JobId,
        tenant_id: TenantId,
        result: IngestJobSkippedDuplicate,
    ) -> IngestJobRecord:
        now = datetime.now(UTC)
        existing = (await self._session.execute(
            select(RagIngestJob.metadata_).where(
                RagIngestJob.job_id == job_id,
                RagIngestJob.tenant_id == tenant_id,
                RagIngestJob.status == "running",
            )
        )).scalar_one_or_none()
        merged = {**(existing if existing else {}), **result.metadata_patch}
        stmt = (
            update(RagIngestJob)
            .where(
                RagIngestJob.job_id == job_id,
                RagIngestJob.tenant_id == tenant_id,
                RagIngestJob.status == "running",
            )
            .values(
                status="skipped_duplicate",
                document_id=result.document_id,
                content_hash=result.content_hash,
                version=result.version,
                parser_used=result.parser_used,
                chunker_used=result.chunker_used,
                parent_chunk_count=result.parent_chunk_count,
                child_chunk_count=result.child_chunk_count,
                warnings=result.warnings,
                parse_report=result.parse_report,
                metadata_=merged,
                finished_at=now,
                updated_at=now,
            )
        )
        update_result = await self._session.execute(stmt)
        if update_result.rowcount == 0:
            msg = f"IngestJob {job_id} not in running state for tenant {tenant_id}"
            raise ValueError(msg)
        row = (await self._session.execute(
            select(RagIngestJob).where(
                RagIngestJob.job_id == job_id,
                RagIngestJob.tenant_id == tenant_id,
            )
        )).scalar_one()
        return _job_to_record(row)

    async def list_recent(
        self,
        tenant_id: TenantId,
        status: str | None = None,
        limit: int = 20,
    ) -> list[IngestJobRecord]:
        stmt = (
            select(RagIngestJob)
            .where(RagIngestJob.tenant_id == tenant_id)
            .order_by(RagIngestJob.created_at.desc())
            .limit(limit)
        )
        if status is not None:
            stmt = stmt.where(RagIngestJob.status == status)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_job_to_record(r) for r in rows]


# ── QueryLogRepository ──────────────────────────────────────────


class QueryLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, input: QueryLogCreate) -> QueryLogRecord:
        row = RagQueryLog(
            request_id=input.request_id,
            tenant_id=input.tenant_id,
            user_id=input.user_id,
            department=input.department,
            access_level=input.access_level,
            question=input.question,
            rewritten_query=input.rewritten_query,
            filters=input.filters,
            client_filters=input.client_filters,
            search_mode=input.search_mode,
            embedding_provider=input.embedding_provider,
            embedding_model=input.embedding_model,
            embedding_dim=input.embedding_dim,
            reranker_provider=input.reranker_provider,
            reranker_model=input.reranker_model,
            top_k=input.top_k,
            final_top_k=input.final_top_k,
            min_rerank_score=input.min_rerank_score,
            min_top1_margin=input.min_top1_margin,
            max_context_tokens=input.max_context_tokens,
            hit_summary=input.hit_summary,
            selected_references=input.selected_references,
            answer=input.answer,
            refusal_reason=input.refusal_reason,
            latencies_ms=input.latencies_ms,
            metadata_=input.metadata,
            status=input.status,
            error_message=input.error_message,
        )
        self._session.add(row)
        await self._session.flush()
        return _query_log_to_record(row)

    async def get_by_request_id(
        self,
        request_id: RequestId,
        tenant_id: TenantId,
    ) -> QueryLogRecord | None:
        stmt = select(RagQueryLog).where(
            RagQueryLog.request_id == request_id,
            RagQueryLog.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _query_log_to_record(row) if row else None

    async def list_recent(
        self,
        tenant_id: TenantId,
        user_id: str | None = None,
        limit: int = 20,
    ) -> list[QueryLogRecord]:
        stmt = (
            select(RagQueryLog)
            .where(RagQueryLog.tenant_id == tenant_id)
            .order_by(RagQueryLog.created_at.desc())
            .limit(limit)
        )
        if user_id is not None:
            stmt = stmt.where(RagQueryLog.user_id == user_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_query_log_to_record(r) for r in rows]

    async def mark_failed(
        self,
        request_id: RequestId,
        tenant_id: TenantId,
        error_message: str,
        latencies_ms: Mapping[str, int],
    ) -> QueryLogRecord:
        stmt = (
            update(RagQueryLog)
            .where(
                RagQueryLog.request_id == request_id,
                RagQueryLog.tenant_id == tenant_id,
            )
            .values(
                status="failed",
                error_message=error_message,
                latencies_ms=dict(latencies_ms),
            )
        )
        await self._session.execute(stmt)
        row = (await self._session.execute(
            select(RagQueryLog).where(
                RagQueryLog.request_id == request_id,
                RagQueryLog.tenant_id == tenant_id,
            )
        )).scalar_one()
        return _query_log_to_record(row)

    async def update_answer(
        self,
        request_id: RequestId,
        tenant_id: TenantId,
        answer: str,
    ) -> QueryLogRecord:
        stmt = (
            update(RagQueryLog)
            .where(
                RagQueryLog.request_id == request_id,
                RagQueryLog.tenant_id == tenant_id,
                RagQueryLog.status == "retrieved",
            )
            .values(status="success", answer=answer)
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            msg = f"QueryLog {request_id} not found in retrieved state for tenant {tenant_id}"
            raise ValueError(msg)
        row = (await self._session.execute(
            select(RagQueryLog).where(
                RagQueryLog.request_id == request_id,
                RagQueryLog.tenant_id == tenant_id,
            )
        )).scalar_one()
        return _query_log_to_record(row)
