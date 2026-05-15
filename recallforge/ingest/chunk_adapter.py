"""Map ChunkFlow packages into storage repository create records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from recallforge.chunking.ir.models import ChildChunk, ChunkPackage, ParentChunk
from recallforge.ingest.errors import ChunkKeyConflictError
from recallforge.ingest.hashing import compute_content_hash
from recallforge.storage.repository import ChildChunkCreate, ParentChunkCreate


@dataclass(frozen=True)
class IngestContext:
    tenant_id: str
    user_id: str | None
    source_uri: str
    source_name: str | None
    doc_type: str
    department: str
    access_level: str
    document_version: int
    embedding_provider: str
    embedding_model: str
    embedding_dim: int


@dataclass
class ChildChunkDraft:
    """Adapter output before parent rows exist."""

    tenant_id: str
    parent_key: str
    chunk_key: str
    chunk_index: int
    content: str
    content_hash: str
    doc_type: str
    department: str
    access_level: str
    source_uri: str
    version: int
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    chunk_type: str = "child"
    template: str | None = None
    heading_path: list[str] | None = None
    page_start: int | None = None
    page_end: int | None = None
    embedding_metadata: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_create(self, parent_id: int) -> ChildChunkCreate:
        return ChildChunkCreate(
            tenant_id=self.tenant_id,
            parent_id=parent_id,
            parent_key=self.parent_key,
            chunk_key=self.chunk_key,
            chunk_index=self.chunk_index,
            content=self.content,
            content_hash=self.content_hash,
            doc_type=self.doc_type,
            department=self.department,
            access_level=self.access_level,
            source_uri=self.source_uri,
            embedding_provider=self.embedding_provider,
            embedding_model=self.embedding_model,
            embedding_dim=self.embedding_dim,
            version=self.version,
            chunk_type=self.chunk_type,
            template=self.template,
            heading_path=self.heading_path,
            page_start=self.page_start,
            page_end=self.page_end,
            embedding_metadata=self.embedding_metadata,
            metadata=self.metadata,
        )


@dataclass
class IngestChunks:
    parent_creates: list[ParentChunkCreate]
    child_drafts_by_parent_key: dict[str, list[ChildChunkDraft]]


def build_chunks_for_ingest(package: ChunkPackage, ctx: IngestContext) -> IngestChunks:
    _validate_unique_keys(package)
    parent_creates = [_parent_to_create(parent, index, ctx) for index, parent in enumerate(package.parent_chunks)]

    child_drafts_by_parent_key: dict[str, list[ChildChunkDraft]] = {}
    child_indexes: dict[str, int] = {}
    for child in package.child_chunks:
        index = child_indexes.get(child.parent_id, 0)
        child_indexes[child.parent_id] = index + 1
        child_drafts_by_parent_key.setdefault(child.parent_id, []).append(_child_to_draft(child, index, ctx))

    known_parent_keys = {parent.parent_key for parent in parent_creates}
    missing_parent_keys = set(child_drafts_by_parent_key) - known_parent_keys
    if missing_parent_keys:
        keys = ", ".join(sorted(missing_parent_keys))
        raise ValueError(f"Child chunks reference missing parent keys: {keys}")

    return IngestChunks(
        parent_creates=parent_creates,
        child_drafts_by_parent_key=child_drafts_by_parent_key,
    )


def _parent_to_create(parent: ParentChunk, index: int, ctx: IngestContext) -> ParentChunkCreate:
    page_start, page_end = _page_span(parent.page_span)
    return ParentChunkCreate(
        tenant_id=ctx.tenant_id,
        source_uri=ctx.source_uri,
        doc_type=ctx.doc_type,
        parent_key=parent.parent_id,
        chunk_index=index,
        content=parent.text,
        content_hash=compute_content_hash(parent.text),
        department=ctx.department,
        access_level=ctx.access_level,
        heading_path=list(parent.heading_path) if parent.heading_path else None,
        page_start=page_start,
        page_end=page_end,
        token_count=_optional_int(parent.metadata.get("token_count")),
        version=ctx.document_version,
        metadata={
            **dict(parent.metadata),
            "title": parent.title,
            "section_id": parent.section_id,
            "source_block_ids": list(parent.source_block_ids),
            "child_chunk_ids": list(parent.child_chunk_ids),
        },
    )


def _child_to_draft(child: ChildChunk, index: int, ctx: IngestContext) -> ChildChunkDraft:
    page_start, page_end = _page_span(child.page_span)
    return ChildChunkDraft(
        tenant_id=ctx.tenant_id,
        parent_key=child.parent_id,
        chunk_key=child.chunk_id,
        chunk_index=index,
        content=child.text,
        content_hash=compute_content_hash(child.text),
        doc_type=ctx.doc_type,
        department=ctx.department,
        access_level=ctx.access_level,
        source_uri=ctx.source_uri,
        version=ctx.document_version,
        embedding_provider=ctx.embedding_provider,
        embedding_model=ctx.embedding_model,
        embedding_dim=ctx.embedding_dim,
        chunk_type="child",
        template=child.template,
        heading_path=list(child.heading_path) if child.heading_path else None,
        page_start=page_start,
        page_end=page_end,
        metadata={
            **dict(child.metadata),
            "chunkflow_chunk_type": child.chunk_type,
            "source_block_ids": list(child.source_block_ids),
            "bbox_refs": [ref.to_dict() for ref in child.bbox_refs],
            "context_before": child.context_before,
            "context_after": child.context_after,
            "token_count": child.token_count,
        },
    )


def _validate_unique_keys(package: ChunkPackage) -> None:
    parent_keys = [parent.parent_id for parent in package.parent_chunks]
    child_keys = [child.chunk_id for child in package.child_chunks]
    duplicate_parent_keys = _duplicates(parent_keys)
    duplicate_child_keys = _duplicates(child_keys)
    if duplicate_parent_keys or duplicate_child_keys:
        raise ChunkKeyConflictError(
            duplicate_parent_keys=duplicate_parent_keys,
            duplicate_child_keys=duplicate_child_keys,
        )


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _page_span(value: tuple[int, int] | list[int] | None) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    start, end = int(value[0]), int(value[1])
    return start if start > 0 else None, end if end > 0 else None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
