"""M3 embedding column routing tests."""

from __future__ import annotations

import pytest

from recallforge.storage.embedding_columns import (
    BASELINE_EMBEDDING_SPEC,
    DEFAULT_EMBEDDING_COLUMNS,
    EmbeddingColumnRegistry,
    UnknownEmbeddingModelError,
)


def test_baseline_embedding_column_route():
    spec = DEFAULT_EMBEDDING_COLUMNS.resolve("text-embedding-v4@1024")

    assert spec == BASELINE_EMBEDDING_SPEC
    assert spec.column_name == "embedding_text_embedding_v4_1024"
    assert spec.dim == 1024
    assert spec.distance_metric == "cosine"


def test_unknown_embedding_model_fails_fast():
    with pytest.raises(UnknownEmbeddingModelError, match="unknown embedding_model"):
        DEFAULT_EMBEDDING_COLUMNS.resolve("text-embedding-v4@2048")


def test_sqlalchemy_model_column_dimension_matches_route():
    EmbeddingColumnRegistry().validate_sqlalchemy_model(BASELINE_EMBEDDING_SPEC)
