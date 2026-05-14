"""Embedding provider contracts for RecallForge M3."""

from __future__ import annotations

from typing import Literal, Protocol, Sequence

DistanceMetric = Literal["cosine", "l2", "inner_product"]
EmbeddingTextType = Literal["document", "query"]


class EmbeddingProviderError(RuntimeError):
    """Base error raised by embedding providers."""


class EmbeddingConfigurationError(EmbeddingProviderError):
    """Raised when provider configuration is invalid."""


class EmbeddingDimensionMismatch(EmbeddingProviderError):
    """Raised when an embedding vector does not match the configured dimension."""

    def __init__(
        self,
        message: str,
        *,
        expected_dim: int | None = None,
        actual_dim: int | None = None,
        embedding_model: str | None = None,
    ) -> None:
        super().__init__(message)
        self.expected_dim = expected_dim
        self.actual_dim = actual_dim
        self.embedding_model = embedding_model


class EmbeddingProvider(Protocol):
    provider: str
    name: str
    model_slug: str
    dim: int
    max_input_tokens: int
    distance_metric: DistanceMetric

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed stored document chunks using provider-specific document mode."""

    async def embed_query(self, text: str) -> list[float]:
        """Embed a user query using provider-specific query mode."""

    async def preflight(self) -> None:
        """Validate provider configuration and remote availability."""


def validate_embedding_dimensions(
    vectors: Sequence[Sequence[float]],
    *,
    expected_count: int,
    expected_dim: int,
    embedding_model: str,
) -> list[list[float]]:
    if len(vectors) != expected_count:
        raise EmbeddingDimensionMismatch(
            "embedding provider returned an unexpected vector count "
            f"for embedding_model={embedding_model}: expected {expected_count}, got {len(vectors)}",
            expected_dim=expected_dim,
            embedding_model=embedding_model,
        )

    normalized: list[list[float]] = []
    for index, vector in enumerate(vectors):
        vector_list = [float(value) for value in vector]
        if len(vector_list) != expected_dim:
            raise EmbeddingDimensionMismatch(
                "embedding provider returned a vector with the wrong dimension "
                f"for embedding_model={embedding_model}, index={index}: "
                f"expected {expected_dim}, got {len(vector_list)}",
                expected_dim=expected_dim,
                actual_dim=len(vector_list),
                embedding_model=embedding_model,
            )
        normalized.append(vector_list)
    return normalized
