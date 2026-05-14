"""Embedding provider factory."""

from __future__ import annotations

from recallforge.config import Settings
from recallforge.embeddings.alibaba_bailian import AlibabaBailianEmbeddingProvider
from recallforge.embeddings.provider import EmbeddingConfigurationError, EmbeddingProvider


def embedding_provider_from_settings(settings: Settings) -> EmbeddingProvider:
    provider = settings.embedding_provider.strip().lower()
    if provider in {"dashscope", "alibaba_bailian", "bailian"}:
        return AlibabaBailianEmbeddingProvider(settings)
    raise EmbeddingConfigurationError(f"unsupported embedding_provider={settings.embedding_provider!r}")
