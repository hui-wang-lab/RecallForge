"""Reranker provider factory."""

from __future__ import annotations

from recallforge.config import Settings
from recallforge.retrieval.reranker.dashscope_reranker import DashScopeRerankerProvider
from recallforge.retrieval.reranker.provider import RerankerProvider


def reranker_provider_from_settings(settings: Settings) -> RerankerProvider | None:
    if not settings.reranker_model:
        return None
    provider = settings.reranker_provider or "dashscope"
    if provider != "dashscope":
        raise ValueError(f"unsupported reranker_provider={provider!r}")
    return DashScopeRerankerProvider(settings)
