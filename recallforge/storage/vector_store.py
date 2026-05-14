"""Vector store abstractions for RecallForge M3."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from recallforge.embeddings.provider import EmbeddingDimensionMismatch

TenantId = str
DocumentId = int
ParentChunkId = int
ChunkId = int

VECTOR_METADATA_FIELDS = frozenset(
    {
        "tenant_id",
        "document_id",
        "chunk_id",
        "chunk_key",
        "parent_id",
        "parent_key",
        "doc_type",
        "chunk_type",
        "template",
        "access_level",
        "department",
        "heading_path",
        "page_start",
        "page_end",
        "source_uri",
        "version",
        "embedding_model",
        "embedding_provider",
        "embedding_dim",
        "status",
    }
)


class VectorStoreError(RuntimeError):
    """Base error raised by vector stores."""


class VectorFilterError(VectorStoreError):
    """Raised when vector filters are invalid or tenant ownership is violated."""


class VectorMetadataError(VectorStoreError):
    """Raised when vector metadata contains forbidden or unknown fields."""


class VectorUpsertConflict(VectorStoreError):
    """Raised when a chunk cannot be updated safely."""


class VectorSearchError(VectorStoreError):
    """Raised when vector search cannot be executed."""


class UnsupportedSearchModeError(VectorSearchError):
    """Raised when a search mode is intentionally not implemented in M3."""


@dataclass(frozen=True)
class VectorChunk:
    chunk_id: ChunkId
    tenant_id: TenantId
    document_id: DocumentId
    parent_id: ParentChunkId
    chunk_key: str
    parent_key: str
    embedding: list[float]
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    metadata: dict[str, Any] = field(default_factory=dict)
    latency_ms: int | None = None
    retry_count: int = 0
    backfill_run_id: str | None = None

    def __post_init__(self) -> None:
        if self.embedding_dim <= 0:
            raise EmbeddingDimensionMismatch(
                f"embedding_dim must be positive for chunk_id={self.chunk_id}",
                expected_dim=self.embedding_dim,
                actual_dim=len(self.embedding),
                embedding_model=self.embedding_model,
            )
        if len(self.embedding) != self.embedding_dim:
            raise EmbeddingDimensionMismatch(
                "VectorChunk embedding dimension mismatch "
                f"for chunk_id={self.chunk_id}, embedding_model={self.embedding_model}: "
                f"expected {self.embedding_dim}, got {len(self.embedding)}",
                expected_dim=self.embedding_dim,
                actual_dim=len(self.embedding),
                embedding_model=self.embedding_model,
            )
        validate_vector_metadata(self.metadata)


@dataclass(frozen=True)
class VectorSearchHit:
    chunk_id: ChunkId
    document_id: DocumentId
    parent_id: ParentChunkId
    chunk_key: str
    parent_key: str
    rank: int
    score: float
    distance: float
    score_source: str
    metadata: dict[str, Any]

    def __post_init__(self) -> None:
        validate_vector_metadata(self.metadata)


@dataclass(frozen=True)
class VectorSearchFilter:
    tenant_id: str
    department: str | list[str] | None = None
    access_level: str | list[str] | None = None
    doc_type: str | None = None
    status: str | None = None
    version: int | None = None
    source_uri: str | None = None
    document_id: DocumentId | None = None


class VectorStoreAdapter(Protocol):
    async def upsert_chunks(self, chunks: Sequence[VectorChunk]) -> None:
        """Write document embeddings for existing child chunks."""

    async def search(
        self,
        query_embedding: Sequence[float],
        embedding_model: str,
        filters: VectorSearchFilter,
        top_k: int,
        search_mode: str = "vector",
    ) -> list[VectorSearchHit]:
        """Search child chunks by a previously generated query embedding."""

    async def delete_by_document_id(self, document_id: DocumentId, tenant_id: TenantId) -> None:
        """Mark a document's vectors as unavailable for retrieval."""


def validate_vector_metadata(metadata: dict[str, Any]) -> None:
    forbidden = set(metadata) - VECTOR_METADATA_FIELDS
    if forbidden:
        if "user_id" in forbidden:
            raise VectorMetadataError("user_id is forbidden in vector metadata")
        raise VectorMetadataError(f"unknown vector metadata keys: {sorted(forbidden)}")
