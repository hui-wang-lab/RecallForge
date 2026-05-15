"""Storage models, repositories, and vector-store adapters."""

from recallforge.storage.embedding_columns import (
    DEFAULT_EMBEDDING_COLUMNS,
    EmbeddingColumnRegistry,
    EmbeddingColumnSpec,
    UnknownEmbeddingModelError,
)
from recallforge.storage.pgvector_store import PgVectorStore
from recallforge.storage.vector_store import (
    UnsupportedSearchModeError,
    VectorChunk,
    VectorFilterError,
    VectorMetadataError,
    VectorSearchError,
    VectorSearchFilter,
    VectorSearchHit,
    VectorStoreAdapter,
    VectorStoreError,
    VectorUpsertConflict,
)

__all__ = [
    "DEFAULT_EMBEDDING_COLUMNS",
    "EmbeddingColumnRegistry",
    "EmbeddingColumnSpec",
    "PgVectorStore",
    "UnknownEmbeddingModelError",
    "UnsupportedSearchModeError",
    "VectorChunk",
    "VectorFilterError",
    "VectorMetadataError",
    "VectorSearchError",
    "VectorSearchFilter",
    "VectorSearchHit",
    "VectorStoreAdapter",
    "VectorStoreError",
    "VectorUpsertConflict",
]
