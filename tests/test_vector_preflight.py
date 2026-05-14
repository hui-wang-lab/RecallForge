"""M3 vector preflight tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from recallforge.config import Settings
from recallforge.embeddings.provider import EmbeddingConfigurationError
from recallforge.storage.embedding_columns import EmbeddingColumnRegistry, EmbeddingColumnSpec
from recallforge.storage.vector_preflight import validate_embedding_dimensions


@dataclass
class _Provider:
    provider: str = "dashscope"
    name: str = "text-embedding-v4@1024"
    model_slug: str = "text_embedding_v4_1024"
    dim: int = 1024
    max_input_tokens: int = 8192
    distance_metric: str = "cosine"

    async def embed_documents(self, texts):
        return []

    async def embed_query(self, text):
        return []

    async def preflight(self):
        return None


@pytest.mark.asyncio
async def test_preflight_accepts_baseline_dimensions_without_live_db():
    await validate_embedding_dimensions(Settings(openai_api_key="test"), _Provider(), session=None)


@pytest.mark.asyncio
async def test_preflight_rejects_settings_provider_dimension_mismatch():
    with pytest.raises(EmbeddingConfigurationError, match="embedding_dim"):
        await validate_embedding_dimensions(
            Settings(openai_api_key="test", embedding_dim=768),
            _Provider(),
            session=None,
        )


@pytest.mark.asyncio
async def test_preflight_rejects_column_route_dimension_mismatch():
    columns = EmbeddingColumnRegistry(
        [
            EmbeddingColumnSpec(
                provider="dashscope",
                model="text-embedding-v4@1024",
                model_slug="text_embedding_v4_1024",
                column_name="embedding_text_embedding_v4_1024",
                dim=768,
                distance_metric="cosine",
            )
        ]
    )

    with pytest.raises(EmbeddingConfigurationError, match="dimension mismatch"):
        await validate_embedding_dimensions(Settings(openai_api_key="test"), _Provider(), columns, session=None)
