"""Postgres + pgvector implementation of VectorStoreAdapter."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import false, select
from sqlalchemy.ext.asyncio import AsyncSession

from recallforge.embeddings.provider import EmbeddingDimensionMismatch
from recallforge.storage.embedding_columns import (
    DEFAULT_EMBEDDING_COLUMNS,
    EmbeddingColumnRegistry,
    EmbeddingColumnSpec,
)
from recallforge.storage.models import DOCUMENT_STATUSES, RagChunk, RagDocument
from recallforge.storage.vector_store import (
    UnsupportedSearchModeError,
    VectorChunk,
    VectorFilterError,
    VectorMetadataError,
    VectorSearchError,
    VectorSearchFilter,
    VectorSearchHit,
    VectorUpsertConflict,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class PgVectorStore:
    """VectorStoreAdapter backed by rag_chunks pgvector columns."""

    def __init__(
        self,
        session: AsyncSession,
        columns: EmbeddingColumnRegistry = DEFAULT_EMBEDDING_COLUMNS,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session = session
        self._columns = columns
        self._clock = clock

    async def upsert_chunks(self, chunks: Sequence[VectorChunk]) -> None:
        if not chunks:
            return

        specs_by_model = {chunk.embedding_model: self._columns.resolve(chunk.embedding_model) for chunk in chunks}
        for chunk in chunks:
            self._validate_vector_chunk(chunk, specs_by_model[chunk.embedding_model])

        duplicate_ids = [
            chunk_id
            for chunk_id, count in Counter(chunk.chunk_id for chunk in chunks).items()
            if count > 1
        ]
        if duplicate_ids:
            raise VectorUpsertConflict(f"duplicate chunk ids in vector upsert: {duplicate_ids[:10]}")

        chunk_ids = [chunk.chunk_id for chunk in chunks]
        rows = (
            (await self._session.execute(select(RagChunk).where(RagChunk.id.in_(chunk_ids)).with_for_update()))
            .scalars()
            .all()
        )
        rows_by_id = {row.id: row for row in rows}
        conflicts: list[int] = []
        for chunk in chunks:
            row = rows_by_id.get(chunk.chunk_id)
            if row is None or row.tenant_id != chunk.tenant_id or row.status != "active":
                conflicts.append(chunk.chunk_id)
        if conflicts:
            raise VectorUpsertConflict(
                "cannot upsert vectors for missing, cross-tenant, or inactive chunks; "
                f"chunk_ids_sample={conflicts[:10]}"
            )

        now = self._clock()
        for chunk in chunks:
            spec = specs_by_model[chunk.embedding_model]
            row = rows_by_id[chunk.chunk_id]
            setattr(row, spec.column_name, list(chunk.embedding))
            if spec.column_name == self._columns.default.column_name:
                row.embedding_provider = spec.provider
                row.embedding_model = spec.model
                row.embedding_dim = spec.dim
            row.embedding_metadata = self._merged_embedding_metadata(row.embedding_metadata, spec, chunk, now)
            row.updated_at = now

        await self._session.flush()

    async def search(
        self,
        query_embedding: Sequence[float],
        embedding_model: str,
        filters: VectorSearchFilter,
        top_k: int,
        search_mode: str = "vector",
    ) -> list[VectorSearchHit]:
        if search_mode != "vector":
            raise UnsupportedSearchModeError(f"unsupported search_mode={search_mode!r}; M3 only supports 'vector'")
        if top_k <= 0:
            raise VectorSearchError(f"top_k must be positive, got {top_k}")

        spec = self._columns.resolve(embedding_model)
        query_vector = [float(value) for value in query_embedding]
        if len(query_vector) != spec.dim:
            raise EmbeddingDimensionMismatch(
                "query embedding dimension mismatch "
                f"for embedding_model={embedding_model}: expected {spec.dim}, got {len(query_vector)}",
                expected_dim=spec.dim,
                actual_dim=len(query_vector),
                embedding_model=embedding_model,
            )

        clauses = self._filter_clauses(filters)
        vector_column = getattr(RagChunk, spec.column_name)
        distance = vector_column.cosine_distance(query_vector).label("distance")
        stmt = (
            select(RagChunk, distance)
            .where(*clauses, vector_column.is_not(None))
            .order_by(distance)
            .limit(top_k)
        )

        result = await self._session.execute(stmt)
        hits: list[VectorSearchHit] = []
        for rank, (row, raw_distance) in enumerate(result.all(), start=1):
            distance_value = float(raw_distance)
            hits.append(
                VectorSearchHit(
                    chunk_id=row.id,
                    document_id=row.document_id,
                    parent_id=row.parent_id,
                    chunk_key=row.chunk_key,
                    parent_key=row.parent_key,
                    rank=rank,
                    distance=distance_value,
                    score=1.0 - distance_value,
                    score_source="vector",
                    metadata=_metadata_from_row(row, spec),
                )
            )
        return hits

    async def delete_by_document_id(self, document_id: int, tenant_id: str) -> None:
        document = (
            await self._session.execute(select(RagDocument).where(RagDocument.id == document_id))
        ).scalar_one_or_none()
        if document is not None and document.tenant_id != tenant_id:
            raise VectorFilterError(
                f"document_id={document_id} does not belong to tenant_id={tenant_id!r}"
            )
        if document is None:
            return

        rows = (
            (
                await self._session.execute(
                    select(RagChunk)
                    .where(RagChunk.document_id == document_id, RagChunk.tenant_id == tenant_id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return

        now = self._clock()
        deleted_at = now.isoformat()
        for row in rows:
            metadata = row.embedding_metadata if isinstance(row.embedding_metadata, dict) else {}
            merged = dict(metadata)
            for spec in self._columns.all_specs():
                previous = merged.get(spec.column_name)
                entry = dict(previous) if isinstance(previous, dict) else {}
                entry.update(
                    {
                        "status": "deleted",
                        "deleted_at": deleted_at,
                        "delete_reason": "document_deleted",
                        "provider": spec.provider,
                        "model": spec.model,
                        "model_slug": spec.model_slug,
                        "dim": spec.dim,
                        "distance_metric": spec.distance_metric,
                    }
                )
                merged[spec.column_name] = entry
            row.embedding_metadata = merged
            row.status = "deleted"
            row.deleted_at = now
            row.updated_at = now

        await self._session.flush()

    def _validate_vector_chunk(self, chunk: VectorChunk, spec: EmbeddingColumnSpec) -> None:
        if chunk.embedding_provider != spec.provider:
            raise VectorUpsertConflict(
                f"embedding provider mismatch for chunk_id={chunk.chunk_id}: "
                f"expected {spec.provider}, got {chunk.embedding_provider}"
            )
        if chunk.embedding_model != spec.model:
            raise VectorUpsertConflict(
                f"embedding model mismatch for chunk_id={chunk.chunk_id}: "
                f"expected {spec.model}, got {chunk.embedding_model}"
            )
        if chunk.embedding_dim != spec.dim or len(chunk.embedding) != spec.dim:
            raise EmbeddingDimensionMismatch(
                "VectorChunk embedding dimension mismatch "
                f"for chunk_id={chunk.chunk_id}, embedding_model={spec.model}: "
                f"expected {spec.dim}, got dim={chunk.embedding_dim}, len={len(chunk.embedding)}",
                expected_dim=spec.dim,
                actual_dim=len(chunk.embedding),
                embedding_model=spec.model,
            )

    def _merged_embedding_metadata(
        self,
        existing: Any,
        spec: EmbeddingColumnSpec,
        chunk: VectorChunk,
        now: datetime,
    ) -> dict[str, Any]:
        if existing is not None and not isinstance(existing, dict):
            raise VectorMetadataError(
                f"embedding_metadata must be a JSON object for chunk_id={chunk.chunk_id}"
            )
        merged = dict(existing or {})
        merged[spec.column_name] = {
            "status": "succeeded",
            "provider": spec.provider,
            "model": spec.model,
            "model_slug": spec.model_slug,
            "dim": spec.dim,
            "distance_metric": spec.distance_metric,
            "text_type": "document",
            "backfilled_at": now.isoformat(),
            "latency_ms": chunk.latency_ms,
            "retry_count": chunk.retry_count,
            "backfill_run_id": chunk.backfill_run_id,
        }
        return merged

    def _filter_clauses(self, filters: VectorSearchFilter) -> list[Any]:
        if not filters.tenant_id:
            raise VectorFilterError("VectorSearchFilter.tenant_id is required")
        clauses: list[Any] = [RagChunk.tenant_id == _safe_string(filters.tenant_id, "tenant_id")]
        status = filters.status or "active"
        if status not in DOCUMENT_STATUSES:
            raise VectorFilterError(f"invalid chunk status filter: {status!r}")
        clauses.append(RagChunk.status == status)

        if filters.knowledge_base_id is not None:
            if isinstance(filters.knowledge_base_id, list):
                clauses.append(RagChunk.knowledge_base_id.in_([int(item) for item in filters.knowledge_base_id]))
            else:
                clauses.append(RagChunk.knowledge_base_id == int(filters.knowledge_base_id))
        if filters.department is not None:
            clauses.append(_single_or_list_clause(RagChunk.department, filters.department, "department"))
        if filters.access_level is not None:
            clauses.append(_single_or_list_clause(RagChunk.access_level, filters.access_level, "access_level"))
        if filters.doc_type is not None:
            clauses.append(RagChunk.doc_type == _safe_string(filters.doc_type, "doc_type"))
        if filters.version is not None:
            clauses.append(RagChunk.version == filters.version)
        if filters.source_uri is not None:
            clauses.append(RagChunk.source_uri == _safe_string(filters.source_uri, "source_uri"))
        if filters.document_id is not None:
            clauses.append(RagChunk.document_id == filters.document_id)
        return clauses


def _metadata_from_row(row: RagChunk, spec: EmbeddingColumnSpec) -> dict[str, Any]:
    return {
        "tenant_id": row.tenant_id,
        "knowledge_base_id": row.knowledge_base_id,
        "document_id": row.document_id,
        "chunk_id": row.id,
        "chunk_key": row.chunk_key,
        "parent_id": row.parent_id,
        "parent_key": row.parent_key,
        "doc_type": row.doc_type,
        "chunk_type": row.chunk_type,
        "template": row.template,
        "access_level": row.access_level,
        "department": row.department,
        "heading_path": row.heading_path,
        "page_start": row.page_start,
        "page_end": row.page_end,
        "source_uri": row.source_uri,
        "version": row.version,
        "embedding_model": spec.model,
        "embedding_provider": spec.provider,
        "embedding_dim": spec.dim,
        "status": row.status,
    }


def _single_or_list_clause(column: Any, value: str | list[str], field_name: str) -> Any:
    if isinstance(value, list):
        if not value:
            return false()
        return column.in_([_safe_string(item, field_name) for item in value])
    return column == _safe_string(value, field_name)


def _safe_string(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise VectorFilterError(f"{field_name} must be a string")
    lowered = value.lower()
    if "\x00" in value or any(marker in lowered for marker in ("--", "/*", "*/", ";")):
        raise VectorFilterError(f"{field_name} contains unsupported SQL-like syntax")
    return value
