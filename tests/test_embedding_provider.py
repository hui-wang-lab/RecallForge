"""M3 embedding provider tests."""

from __future__ import annotations

import httpx
import pytest

from recallforge.config import Settings
from recallforge.embeddings.alibaba_bailian import AlibabaBailianEmbeddingProvider, model_to_slug
from recallforge.embeddings.provider import EmbeddingConfigurationError, EmbeddingDimensionMismatch


@pytest.mark.asyncio
async def test_dashscope_provider_uses_document_and_query_text_type():
    captured: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = httpx.Response(200, request=request, content=request.content).json()
        captured.append(payload)
        texts = payload["input"]["texts"]
        return httpx.Response(
            200,
            json={
                "output": {
                    "embeddings": [
                        {"text_index": index, "embedding": [float(index), 0.0, 0.0, 1.0]}
                        for index, _ in enumerate(texts)
                    ]
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AlibabaBailianEmbeddingProvider(
        Settings(
            openai_api_key="test-key",
            embedding_dim=4,
            embedding_model="text-embedding-v4@1024",
        ),
        client=client,
    )

    docs = await provider.embed_documents(["alpha", "beta"])
    query = await provider.embed_query("question")

    assert docs == [[0.0, 0.0, 0.0, 1.0], [1.0, 0.0, 0.0, 1.0]]
    assert query == [0.0, 0.0, 0.0, 1.0]
    assert captured[0]["parameters"]["text_type"] == "document"
    assert captured[1]["parameters"]["text_type"] == "query"
    await client.aclose()


def test_dashscope_provider_requires_api_key():
    provider = AlibabaBailianEmbeddingProvider(Settings(openai_api_key=""))

    with pytest.raises(EmbeddingConfigurationError, match="API key"):
        provider._validate_configuration()


@pytest.mark.asyncio
async def test_dashscope_provider_validates_returned_dimensions():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": {"embeddings": [{"text_index": 0, "embedding": [1.0, 2.0]}]}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AlibabaBailianEmbeddingProvider(
        Settings(openai_api_key="test-key", embedding_dim=3),
        client=client,
    )

    with pytest.raises(EmbeddingDimensionMismatch):
        await provider.embed_query("question")
    await client.aclose()


def test_model_to_slug_matches_baseline_column_slug():
    assert model_to_slug("text-embedding-v4@1024") == "text_embedding_v4_1024"
