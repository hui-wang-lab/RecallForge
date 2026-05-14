"""Embedding backfill orchestration for M3."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from recallforge.config import Settings
from recallforge.embeddings.provider import EmbeddingConfigurationError, EmbeddingProvider
from recallforge.storage.embedding_columns import DEFAULT_EMBEDDING_COLUMNS, EmbeddingColumnRegistry
from recallforge.storage.pgvector_store import PgVectorStore
from recallforge.storage.repository import ChildChunkEmbeddingSource, ChunkRepository
from recallforge.storage.vector_store import VectorChunk, VectorStoreAdapter

logger = logging.getLogger("recallforge.embeddings.backfill")


class AsyncSessionContext(Protocol):
    async def __aenter__(self) -> AsyncSession: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> object: ...


@dataclass(frozen=True)
class BackfillRequest:
    embedding_model: str
    tenant_id: str | None = None
    chunk_ids: list[int] | None = None
    limit: int = 1000
    batch_size: int | None = None
    force: bool = False


@dataclass(frozen=True)
class BackfillResult:
    embedding_model: str
    attempted: int
    succeeded: int
    failed: int
    skipped: int
    backfill_run_id: str


class _BatchFailure(RuntimeError):
    def __init__(self, attempted: int, cause: Exception) -> None:
        super().__init__(str(cause))
        self.attempted = attempted
        self.__cause__ = cause


class EmbeddingBackfillService:
    def __init__(
        self,
        session_factory: Callable[[], AsyncSessionContext],
        provider: EmbeddingProvider,
        settings: Settings,
        *,
        columns: EmbeddingColumnRegistry = DEFAULT_EMBEDDING_COLUMNS,
        repository_factory: Callable[[AsyncSession], ChunkRepository] = ChunkRepository,
        vector_store_factory: Callable[[AsyncSession, EmbeddingColumnRegistry], VectorStoreAdapter] = PgVectorStore,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._settings = settings
        self._columns = columns
        self._repository_factory = repository_factory
        self._vector_store_factory = vector_store_factory

    async def backfill(self, request: BackfillRequest) -> BackfillResult:
        if request.limit <= 0:
            raise ValueError(f"BackfillRequest.limit must be positive, got {request.limit}")
        if request.embedding_model != self._provider.name:
            raise EmbeddingConfigurationError(
                "backfill request embedding_model must match provider.name: "
                f"request={request.embedding_model}, provider={self._provider.name}"
            )

        spec = self._columns.resolve(request.embedding_model)
        if spec.dim != self._provider.dim or spec.provider != self._provider.provider:
            raise EmbeddingConfigurationError(
                "embedding provider and column route mismatch: "
                f"provider={self._provider.provider}/{self._provider.dim}, "
                f"spec={spec.provider}/{spec.dim}"
            )

        batch_size = request.batch_size or self._settings.embedding_batch_size
        if batch_size <= 0:
            raise ValueError(f"embedding batch size must be positive, got {batch_size}")

        run_id = str(uuid.uuid4())
        attempted = 0
        succeeded = 0
        failed = 0
        skipped = 0
        single_pass = request.force or bool(request.chunk_ids)

        while attempted < request.limit:
            batch_limit = min(batch_size, request.limit - attempted)
            try:
                batch_attempted, batch_succeeded = await self._run_batch(request, batch_limit, run_id)
            except _BatchFailure as exc:
                failed += exc.attempted
                attempted += exc.attempted
                logger.exception(
                    "embedding_backfill_batch_failed",
                    extra={
                        "embedding_model": request.embedding_model,
                        "backfill_run_id": run_id,
                        "attempted": exc.attempted,
                    },
                )
                break

            if batch_attempted == 0:
                break

            attempted += batch_attempted
            succeeded += batch_succeeded
            skipped += batch_attempted - batch_succeeded

            if single_pass or batch_attempted < batch_limit:
                break
            if self._settings.embedding_batch_delay_seconds > 0:
                await asyncio.sleep(self._settings.embedding_batch_delay_seconds)

        return BackfillResult(
            embedding_model=request.embedding_model,
            attempted=attempted,
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            backfill_run_id=run_id,
        )

    async def _run_batch(
        self,
        request: BackfillRequest,
        batch_limit: int,
        backfill_run_id: str,
    ) -> tuple[int, int]:
        async with self._session_factory() as session:
            async with session.begin():
                repository = self._repository_factory(session)
                vector_store = self._vector_store_factory(session, self._columns)
                sources = await repository.list_for_embedding_backfill(
                    request.embedding_model,
                    batch_limit,
                    request.tenant_id,
                    statuses=("active",),
                    columns=self._columns,
                    chunk_ids=request.chunk_ids,
                    force=request.force,
                )
                if not sources:
                    return 0, 0

                started = time.perf_counter()
                try:
                    embeddings = await self._provider.embed_documents([source.content for source in sources])
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    vector_chunks = [
                        _source_to_vector_chunk(
                            source,
                            embeddings[index],
                            provider=self._provider,
                            latency_ms=latency_ms,
                            retry_count=getattr(self._provider, "last_retry_count", 0),
                            backfill_run_id=backfill_run_id,
                        )
                        for index, source in enumerate(sources)
                    ]
                    await vector_store.upsert_chunks(vector_chunks)
                except Exception as exc:
                    raise _BatchFailure(len(sources), exc) from exc

                return len(sources), len(sources)


def _source_to_vector_chunk(
    source: ChildChunkEmbeddingSource,
    embedding: list[float],
    *,
    provider: EmbeddingProvider,
    latency_ms: int,
    retry_count: int,
    backfill_run_id: str,
) -> VectorChunk:
    metadata: dict[str, Any] = {
        "tenant_id": source.tenant_id,
        "document_id": source.document_id,
        "chunk_id": source.id,
        "chunk_key": source.chunk_key,
        "parent_id": source.parent_id,
        "parent_key": source.parent_key,
        "doc_type": source.doc_type,
        "chunk_type": source.chunk_type,
        "template": source.template,
        "access_level": source.access_level,
        "department": source.department,
        "heading_path": source.heading_path,
        "page_start": source.page_start,
        "page_end": source.page_end,
        "source_uri": source.source_uri,
        "version": source.version,
        "embedding_model": provider.name,
        "embedding_provider": provider.provider,
        "embedding_dim": provider.dim,
        "status": source.status,
    }
    return VectorChunk(
        chunk_id=source.id,
        tenant_id=source.tenant_id,
        document_id=source.document_id,
        parent_id=source.parent_id,
        chunk_key=source.chunk_key,
        parent_key=source.parent_key,
        embedding=embedding,
        embedding_provider=provider.provider,
        embedding_model=provider.name,
        embedding_dim=provider.dim,
        metadata=metadata,
        latency_ms=latency_ms,
        retry_count=retry_count,
        backfill_run_id=backfill_run_id,
    )
