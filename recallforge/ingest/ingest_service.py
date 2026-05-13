"""M2 document ingest orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from recallforge.chunking.core.pipeline import available_parsers, parse_to_chunk_package
from recallforge.chunking.ir.models import ChunkPackage
from recallforge.config import Settings
from recallforge.ingest.chunk_adapter import IngestContext, build_chunks_for_ingest
from recallforge.ingest.errors import ChunkKeyConflictError, IngestError, OversizeError, ParserUnavailableError, UnsupportedFileTypeError
from recallforge.ingest.hashing import compute_package_content_hash
from recallforge.ingest.pipeline_config import build_pipeline_config
from recallforge.storage.repository import (
    ChunkRepository,
    DocumentCreate,
    DocumentRepository,
    DocumentVersionRepository,
    IngestJobCreate,
    IngestJobRecord,
    IngestJobRepository,
    IngestJobSkippedDuplicate,
    IngestJobSuccess,
    ParentChunkRepository,
)

logger = logging.getLogger("recallforge.ingest")

SUPPORTED_DOC_TYPES_BY_SUFFIX = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "txt",
    ".pdf": "pdf",
    ".csv": "csv",
    ".tsv": "tsv",
}

KNOWN_PARENT_GRANULARITIES = {"chapter", "section", "document", "paragraph"}

_ADVISORY_LOCK_PREFIX = 0x524543414C4C464F  # "RECALLFO" in hex


class AsyncSessionContext(Protocol):
    async def __aenter__(self) -> AsyncSession: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> object: ...


AsyncSessionFactory = Callable[[], AsyncSessionContext]
ParseFunction = Callable[[Path, Any], ChunkPackage]


@dataclass(frozen=True)
class EmbeddingProviderConfig:
    provider: str
    model_slug: str
    dim: int


@dataclass(kw_only=True)
class IngestRequest:
    tenant_id: str
    user_id: str | None
    source_uri: str
    department: str
    access_level: str
    file_path: Path
    source_name: str | None = None
    doc_type: str | None = None
    title: str | None = None
    created_by: str | None = None
    parser_hint: str = "auto"
    template_hint: str = "auto"
    metadata: dict[str, Any] = field(default_factory=dict)


def _normalize_warning(w: Any) -> dict[str, Any]:
    """Normalize a warning into structured dict form for JSONB storage."""
    if isinstance(w, dict):
        return w
    return {"level": "warning", "message": str(w), "source": "chunkflow"}


class IngestService:
    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        settings: Settings,
        embedding_provider: EmbeddingProviderConfig,
        *,
        parse_function: ParseFunction = parse_to_chunk_package,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._embedding_provider = embedding_provider
        self._parse_function = parse_function
        self._available_parsers: dict[str, bool] = available_parsers()
        logger.info(
            "parser_availability",
            extra={"parsers": self._available_parsers},
        )

    async def ingest_document(self, request: IngestRequest) -> IngestJobRecord:
        # Cheap-fail checks BEFORE job creation to avoid polluting the jobs table
        doc_type = _resolve_doc_type(request.file_path, request.doc_type)
        await self._check_file_size(request.file_path)
        warnings = _preflight_warnings(request, doc_type, self._settings)

        job = await self._create_job(request, doc_type, warnings)
        # Transition to running immediately so terminal-state methods can find the job
        await self._mark_running(job, request.tenant_id)

        try:
            package = await self._parse(request)
            self._check_child_chunk_count(package)
            return await self._persist_with_advisory_lock(request, doc_type, package, warnings, job)
        except Exception as exc:
            await self._mark_failed(job, request.tenant_id, exc, warnings)
            raise

    async def _mark_running(self, job: IngestJobRecord, tenant_id: str) -> None:
        """Transition job from pending to running in its own transaction."""
        async with self._session_factory() as session:
            async with session.begin():
                await IngestJobRepository(session).mark_running(job.job_id, tenant_id)

    @asynccontextmanager
    async def _acquire_advisory_lock(
        self,
        session: AsyncSession,
        tenant_id: str,
        source_uri: str,
    ) -> AsyncGenerator[None, None]:
        lock_id = _compute_advisory_lock_id(tenant_id, source_uri)
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": lock_id},
        )
        yield

    async def _persist_with_advisory_lock(
        self,
        request: IngestRequest,
        doc_type: str,
        package: ChunkPackage,
        preflight_warnings: list[Any],
        job: IngestJobRecord,
    ) -> IngestJobRecord:
        async with self._session_factory() as session:
            async with session.begin():
                async with self._acquire_advisory_lock(session, request.tenant_id, request.source_uri):
                    return await self._persist_success_or_duplicate(
                        session, request, doc_type, package, preflight_warnings, job
                    )

    async def _create_job(
        self,
        request: IngestRequest,
        doc_type: str,
        warnings: list[Any],
    ) -> IngestJobRecord:
        metadata = dict(request.metadata)
        if warnings:
            metadata["preflight_warnings"] = warnings
        async with self._session_factory() as session:
            async with session.begin():
                return await IngestJobRepository(session).create(
                    IngestJobCreate(
                        tenant_id=request.tenant_id,
                        source_uri=request.source_uri,
                        source_name=request.source_name or request.file_path.name,
                        doc_type=doc_type,
                        parser=request.parser_hint,
                        template=request.template_hint,
                        created_by=request.created_by or request.user_id,
                        metadata=metadata,
                    )
                )

    async def _check_file_size(self, file_path: Path) -> None:
        loop = asyncio.get_running_loop()
        file_size = await loop.run_in_executor(None, lambda: file_path.stat().st_size)
        limit = self._settings.ingest_max_file_bytes
        if file_size > limit:
            raise OversizeError(
                message=f"File size {file_size} exceeds ingest limit {limit}",
                limit_name="ingest_max_file_bytes",
                actual=file_size,
                limit=limit,
            )

    async def _parse(self, request: IngestRequest) -> ChunkPackage:
        config = build_pipeline_config(
            self._settings,
            parser_hint=request.parser_hint,
            template_hint=request.template_hint,
        )
        loop = asyncio.get_running_loop()
        task = loop.run_in_executor(None, self._parse_function, request.file_path, config)
        try:
            return await asyncio.wait_for(task, timeout=self._settings.ingest_parse_timeout_seconds)
        except TimeoutError as exc:
            raise ParserUnavailableError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise ParserUnavailableError(str(exc)) from exc
        except RuntimeError as exc:
            if "No parser could parse" in str(exc):
                raise ParserUnavailableError(str(exc)) from exc
            raise

    def _check_child_chunk_count(self, package: ChunkPackage) -> None:
        count = len(package.child_chunks)
        limit = self._settings.ingest_max_child_chunks_per_document
        if count > limit:
            raise OversizeError(
                message=f"Child chunk count {count} exceeds ingest limit {limit}",
                limit_name="ingest_max_child_chunks_per_document",
                actual=count,
                limit=limit,
                phase="chunking",
            )

    async def _persist_success_or_duplicate(
        self,
        session: AsyncSession,
        request: IngestRequest,
        doc_type: str,
        package: ChunkPackage,
        preflight_warnings: list[Any],
        job: IngestJobRecord,
    ) -> IngestJobRecord:
        document_hash = compute_package_content_hash(package)
        package_warnings = [
            *[_normalize_warning(w) for w in preflight_warnings],
            *[_normalize_warning(w) for w in package.warnings],
        ]
        parse_report = package.parse_report.to_dict()

        document_repo = DocumentRepository(session)
        job_repo = IngestJobRepository(session)
        duplicate = await document_repo.find_by_source_hash(
            request.tenant_id,
            request.source_uri,
            document_hash,
            statuses=("active", "superseded"),
        )
        if duplicate is not None:
            return await job_repo.mark_skipped_duplicate(
                job.job_id,
                request.tenant_id,
                IngestJobSkippedDuplicate(
                    document_id=duplicate.id,
                    content_hash=document_hash,
                    version=duplicate.version,
                    parser_used=package.parser_used,
                    chunker_used=package.chunker_used,
                    parent_chunk_count=len(package.parent_chunks),
                    child_chunk_count=len(package.child_chunks),
                    warnings=package_warnings,
                    parse_report=parse_report,
                    metadata_patch={
                        "dedupe_existing_version": duplicate.version,
                        "skipped_reason": "content_hash_match",
                    },
                ),
            )

        await document_repo.lock_active_by_source(request.tenant_id, request.source_uri)
        await DocumentVersionRepository(session).supersede_source(request.tenant_id, request.source_uri)
        version = await document_repo.next_version(request.tenant_id, request.source_uri)
        document = await document_repo.create(
            DocumentCreate(
                tenant_id=request.tenant_id,
                source_uri=request.source_uri,
                source_name=request.source_name or package.metadata.get("filename") or request.file_path.name,
                doc_type=doc_type,
                title=request.title,
                content_hash=document_hash,
                version=version,
                department=request.department,
                access_level=request.access_level,
                metadata={**dict(package.metadata), **dict(request.metadata)},
                created_by=request.created_by or request.user_id,
                updated_by=request.created_by or request.user_id,
            )
        )

        ingest_chunks = build_chunks_for_ingest(
            package,
            IngestContext(
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                source_uri=request.source_uri,
                source_name=request.source_name,
                doc_type=doc_type,
                department=request.department,
                access_level=request.access_level,
                document_version=version,
                embedding_provider=self._embedding_provider.provider,
                embedding_model=self._embedding_provider.model_slug,
                embedding_dim=self._embedding_provider.dim,
            ),
        )
        parent_records = await ParentChunkRepository(session).bulk_create(
            document.id,
            ingest_chunks.parent_creates,
        )
        if len(parent_records) != len(ingest_chunks.parent_creates):
            raise IngestError(
                f"Parent bulk_create returned {len(parent_records)} records, "
                f"expected {len(ingest_chunks.parent_creates)}"
            )
        parent_ids_by_key = {parent.parent_key: parent.id for parent in parent_records}
        child_creates = [
            draft.to_create(parent_ids_by_key[parent_key])
            for parent_key, drafts in ingest_chunks.child_drafts_by_parent_key.items()
            for draft in drafts
        ]
        await ChunkRepository(session).bulk_create(document.id, child_creates)

        return await job_repo.mark_success(
            job.job_id,
            request.tenant_id,
            IngestJobSuccess(
                document_id=document.id,
                content_hash=document_hash,
                version=version,
                parser_used=package.parser_used,
                chunker_used=package.chunker_used,
                parent_chunk_count=len(parent_records),
                child_chunk_count=len(child_creates),
                warnings=package_warnings,
                parse_report=parse_report,
            ),
        )

    async def _mark_failed(
        self,
        job: IngestJobRecord,
        tenant_id: str,
        exc: Exception,
        preflight_warnings: list[Any],
    ) -> None:
        """Mark job as failed in a new transaction. Never raises — logs errors instead."""
        diagnostics = _diagnostics_for_exception(exc)
        warning = {
            "level": "error",
            "message": str(exc),
            "source": "ingest_service",
            "error_type": type(exc).__name__,
        }
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    await IngestJobRepository(session).mark_failed(
                        job.job_id,
                        tenant_id,
                        str(exc),
                        diagnostics,
                        warnings=[*[_normalize_warning(w) for w in preflight_warnings], warning],
                        parse_report=diagnostics,
                    )
        except Exception:
            logger.exception("Failed to mark ingest job %s as failed", job.job_id)


def _compute_advisory_lock_id(tenant_id: str, source_uri: str) -> int:
    key = f"{tenant_id}:{source_uri}".encode("utf-8")
    hash_bytes = hashlib.sha256(key).digest()
    lock_id = int.from_bytes(hash_bytes[:8], byteorder="big") & 0x7FFFFFFFFFFFFFFF
    return _ADVISORY_LOCK_PREFIX ^ lock_id


def embedding_provider_from_settings(settings: Settings) -> EmbeddingProviderConfig:
    return EmbeddingProviderConfig(
        provider=settings.embedding_provider,
        model_slug=settings.embedding_model,
        dim=settings.embedding_dim,
    )


def _resolve_doc_type(file_path: Path, declared_doc_type: str | None) -> str:
    suffix = file_path.suffix.lower()
    inferred = SUPPORTED_DOC_TYPES_BY_SUFFIX.get(suffix)
    if inferred is None:
        raise UnsupportedFileTypeError(suffix)
    return declared_doc_type or inferred


def _preflight_warnings(
    request: IngestRequest,
    doc_type: str,
    settings: Settings,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    inferred = SUPPORTED_DOC_TYPES_BY_SUFFIX.get(request.file_path.suffix.lower())
    if request.doc_type and inferred and request.doc_type != inferred:
        warnings.append(
            {
                "level": "warning",
                "message": f"doc_type '{request.doc_type}' conflicts with file extension '{request.file_path.suffix}'",
                "source": "ingest_service",
            }
        )
    if settings.parent_granularity not in KNOWN_PARENT_GRANULARITIES:
        warnings.append(
            {
                "level": "info",
                "message": f"parent_granularity '{settings.parent_granularity}' is not a known baseline value",
                "source": "ingest_service",
            }
        )
    return warnings


def _diagnostics_for_exception(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, IngestError):
        return exc.diagnostics()
    if isinstance(exc, TimeoutError):
        return {"error_phase": "parse", "error_type": "TimeoutError"}
    return {"error_phase": "ingest", "error_type": type(exc).__name__}
