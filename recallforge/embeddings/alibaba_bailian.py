"""Alibaba Bailian / DashScope embedding provider."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Sequence
from typing import Any

import httpx

from recallforge.config import Settings
from recallforge.embeddings.provider import (
    DistanceMetric,
    EmbeddingConfigurationError,
    EmbeddingProviderError,
    EmbeddingTextType,
    validate_embedding_dimensions,
)


def model_to_slug(model: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z]+", "_", model).strip("_").lower()
    return slug


class AlibabaBailianEmbeddingProvider:
    """DashScope native embedding provider.

    Public methods deliberately split document and query embedding so callers
    cannot choose an arbitrary text_type.
    """

    provider: str = "dashscope"
    max_input_tokens: int = 8192
    distance_metric: DistanceMetric = "cosine"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = settings.embedding_model
        self.model_slug = model_to_slug(settings.embedding_model)
        self.dim = settings.embedding_dim
        self.endpoint = settings.dashscope_endpoint or settings.openai_base_url
        self.region = settings.dashscope_region
        self._api_key = settings.dashscope_api_key or settings.openai_api_key
        self._api_key_source = "dashscope_api_key" if settings.dashscope_api_key else "openai_api_key"
        self._timeout = settings.embedding_request_timeout_seconds
        self._max_retries = settings.embedding_max_retries
        self._requests_per_second = settings.embedding_requests_per_second
        self._last_request_at = 0.0
        self._rate_lock = asyncio.Lock()
        self._client = client
        self._owns_client = client is None
        self.last_retry_count = 0

    @property
    def api_key_source(self) -> str:
        return self._api_key_source

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(texts, "document")

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text], "query")
        return vectors[0]

    async def preflight(self) -> None:
        self._validate_configuration()
        await self._embed(["recallforge preflight"], "query")

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()

    async def _embed(self, texts: Sequence[str], text_type: EmbeddingTextType) -> list[list[float]]:
        self._validate_configuration()
        if not texts:
            return []

        payload = {
            "model": self.name,
            "input": {"texts": list(texts)},
            "parameters": {"text_type": text_type},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        if self._client is None:
            self._client = client

        retry_count = 0
        while True:
            await self._throttle()
            try:
                response = await client.post(self.endpoint, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                if retry_count >= self._max_retries:
                    self.last_retry_count = retry_count
                    raise EmbeddingProviderError(f"DashScope embedding request failed: {exc}") from exc
                retry_count += 1
                await asyncio.sleep(_retry_delay(retry_count))
                continue

            if response.status_code in {429, 500, 502, 503, 504} and retry_count < self._max_retries:
                retry_count += 1
                await asyncio.sleep(_retry_delay(retry_count))
                continue

            if response.status_code >= 400:
                self.last_retry_count = retry_count
                raise EmbeddingProviderError(
                    "DashScope embedding request failed "
                    f"status_code={response.status_code} body={response.text[:300]}"
                )

            self.last_retry_count = retry_count
            vectors = _extract_embeddings(response.json())
            return validate_embedding_dimensions(
                vectors,
                expected_count=len(texts),
                expected_dim=self.dim,
                embedding_model=self.name,
            )

    async def _throttle(self) -> None:
        if self._requests_per_second <= 0:
            return
        async with self._rate_lock:
            min_interval = 1.0 / self._requests_per_second
            now = time.monotonic()
            wait_for = self._last_request_at + min_interval - now
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last_request_at = time.monotonic()

    def _validate_configuration(self) -> None:
        if self.provider != "dashscope":
            raise EmbeddingConfigurationError(f"unsupported embedding provider: {self.provider}")
        if not self._api_key:
            raise EmbeddingConfigurationError("DashScope API key is required for embedding provider dashscope")
        if not self.endpoint:
            raise EmbeddingConfigurationError("DashScope endpoint is required")
        if self.dim <= 0:
            raise EmbeddingConfigurationError(f"embedding_dim must be positive, got {self.dim}")


def _retry_delay(retry_count: int) -> float:
    return min(2.0 ** max(retry_count - 1, 0), 8.0)


def _extract_embeddings(payload: dict[str, Any]) -> list[list[float]]:
    output = payload.get("output")
    if not isinstance(output, dict):
        raise EmbeddingProviderError("DashScope response missing output object")

    embeddings = output.get("embeddings")
    if not isinstance(embeddings, list):
        raise EmbeddingProviderError("DashScope response missing output.embeddings list")

    indexed: list[tuple[int, list[float]]] = []
    plain: list[list[float]] = []
    for position, item in enumerate(embeddings):
        if isinstance(item, dict):
            vector = item.get("embedding")
            text_index = item.get("text_index", position)
            if not isinstance(vector, list):
                raise EmbeddingProviderError("DashScope embedding item missing embedding list")
            indexed.append((int(text_index), vector))
        elif isinstance(item, list):
            plain.append(item)
        else:
            raise EmbeddingProviderError("DashScope embedding item has unsupported shape")

    if indexed and plain:
        raise EmbeddingProviderError("DashScope response mixed indexed and plain embedding shapes")
    if indexed:
        return [vector for _, vector in sorted(indexed, key=lambda pair: pair[0])]
    return plain
