"""DashScope qwen reranker provider."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import httpx

from recallforge.config import Settings
from recallforge.retrieval.errors import RerankerConfigurationError, RerankerProviderError
from recallforge.retrieval.reranker.provider import RerankedCandidate, RerankerInput


class DashScopeRerankerProvider:
    provider = "dashscope"
    max_candidates = 500

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        self.name = settings.reranker_model
        self.endpoint = settings.reranker_endpoint or "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
        self.region = settings.dashscope_region
        self._api_key = settings.reranker_api_key or settings.dashscope_api_key or settings.openai_api_key
        self._timeout = settings.reranker_request_timeout_seconds
        self._max_retries = settings.reranker_max_retries
        self._client = client
        self._owns_client = client is None
        self.last_retry_count = 0
        self.last_latency_ms = 0

    async def preflight(self) -> None:
        self._validate_configuration()
        await self.rerank("recallforge preflight", [RerankerInput(1, "recallforge preflight", 1, 1.0)], top_k=1)

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()

    async def rerank(
        self,
        query: str,
        candidates: Sequence[RerankerInput],
        top_k: int | None = None,
    ) -> list[RerankedCandidate]:
        self._validate_configuration()
        if not candidates:
            return []
        limited = list(candidates[: self.max_candidates])
        top_n = min(top_k or len(limited), len(limited))
        payload = {
            "model": self.name,
            "input": {
                "query": query,
                "documents": [candidate.content for candidate in limited],
            },
            "parameters": {"top_n": top_n},
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        if self._client is None:
            self._client = client

        retry_count = 0
        while True:
            try:
                response = await client.post(self.endpoint, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                if retry_count >= self._max_retries:
                    self.last_retry_count = retry_count
                    raise RerankerProviderError(f"DashScope reranker request failed: {exc}") from exc
                retry_count += 1
                await asyncio.sleep(_retry_delay(retry_count))
                continue

            if response.status_code in {429, 500, 502, 503, 504} and retry_count < self._max_retries:
                retry_count += 1
                await asyncio.sleep(_retry_delay(retry_count))
                continue
            if response.status_code >= 400:
                self.last_retry_count = retry_count
                raise RerankerProviderError(
                    "DashScope reranker request failed "
                    f"status_code={response.status_code} body={response.text[:300]}"
                )
            self.last_retry_count = retry_count
            return _extract_reranked(response.json(), limited)

    def _validate_configuration(self) -> None:
        if not self.name:
            raise RerankerConfigurationError("reranker_model is required")
        if not self._api_key:
            raise RerankerConfigurationError("DashScope API key is required for reranker")
        if not self.endpoint:
            raise RerankerConfigurationError("DashScope reranker endpoint is required")


def _extract_reranked(payload: dict[str, Any], candidates: Sequence[RerankerInput]) -> list[RerankedCandidate]:
    output = payload.get("output")
    if not isinstance(output, dict):
        raise RerankerProviderError("DashScope reranker response missing output object")
    raw_results = output.get("results") or output.get("rerank_results") or output.get("scores")
    if not isinstance(raw_results, list):
        raise RerankerProviderError("DashScope reranker response missing results list")

    mapped: list[RerankedCandidate] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            raise RerankerProviderError("DashScope reranker result has unsupported shape")
        index = raw.get("index", raw.get("document_index", raw.get("text_index")))
        score = raw.get("relevance_score", raw.get("score"))
        if index is None or score is None:
            raise RerankerProviderError("DashScope reranker result missing index or score")
        candidate = candidates[int(index)]
        mapped.append(
            RerankedCandidate(
                chunk_id=candidate.chunk_id,
                rerank_score=float(score),
                rerank_rank=0,
                original_rank=candidate.original_rank,
                original_score=candidate.original_score,
            )
        )

    mapped.sort(key=lambda item: item.rerank_score, reverse=True)
    return [
        RerankedCandidate(
            chunk_id=item.chunk_id,
            rerank_score=item.rerank_score,
            rerank_rank=rank,
            original_rank=item.original_rank,
            original_score=item.original_score,
        )
        for rank, item in enumerate(mapped, start=1)
    ]


def _retry_delay(retry_count: int) -> float:
    return min(2.0 ** max(retry_count - 1, 0), 8.0)
