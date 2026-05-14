"""Embedding model to pgvector column routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from pgvector.sqlalchemy import Vector

from recallforge.embeddings.provider import DistanceMetric
from recallforge.storage.models import RagChunk


class UnknownEmbeddingModelError(ValueError):
    """Raised when an embedding model has no configured vector column."""

    def __init__(self, embedding_model: str, supported_models: Iterable[str]) -> None:
        supported = sorted(supported_models)
        super().__init__(
            f"unknown embedding_model={embedding_model!r}; supported models: {', '.join(supported) or '(none)'}"
        )
        self.embedding_model = embedding_model
        self.supported_models = supported


@dataclass(frozen=True)
class EmbeddingColumnSpec:
    provider: str
    model: str
    model_slug: str
    column_name: str
    dim: int
    distance_metric: DistanceMetric


BASELINE_EMBEDDING_SPEC = EmbeddingColumnSpec(
    provider="dashscope",
    model="text-embedding-v4@1024",
    model_slug="text_embedding_v4_1024",
    column_name="embedding_text_embedding_v4_1024",
    dim=1024,
    distance_metric="cosine",
)


class EmbeddingColumnRegistry:
    """Authoritative in-process route from embedding_model to vector column."""

    def __init__(self, specs: Iterable[EmbeddingColumnSpec] | None = None) -> None:
        self._specs_by_model = {spec.model: spec for spec in (specs or [BASELINE_EMBEDDING_SPEC])}
        self._specs_by_column = {spec.column_name: spec for spec in self._specs_by_model.values()}

    @property
    def default(self) -> EmbeddingColumnSpec:
        return self.resolve(BASELINE_EMBEDDING_SPEC.model)

    def resolve(self, embedding_model: str) -> EmbeddingColumnSpec:
        try:
            return self._specs_by_model[embedding_model]
        except KeyError as exc:
            raise UnknownEmbeddingModelError(embedding_model, self._specs_by_model) from exc

    def all_specs(self) -> tuple[EmbeddingColumnSpec, ...]:
        return tuple(self._specs_by_model.values())

    def validate_sqlalchemy_model(self, spec: EmbeddingColumnSpec) -> None:
        column = getattr(RagChunk, spec.column_name, None)
        if column is None:
            raise ValueError(f"RagChunk is missing vector column {spec.column_name}")

        column_type = RagChunk.__table__.c[spec.column_name].type
        if not isinstance(column_type, Vector):
            raise ValueError(f"RagChunk.{spec.column_name} must be pgvector Vector, got {column_type!r}")
        if column_type.dim != spec.dim:
            raise ValueError(
                f"RagChunk.{spec.column_name} dimension mismatch: expected {spec.dim}, got {column_type.dim}"
            )


DEFAULT_EMBEDDING_COLUMNS = EmbeddingColumnRegistry()
