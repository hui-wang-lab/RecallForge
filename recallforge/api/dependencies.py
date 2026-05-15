"""Dependency assembly for the M5 FastAPI app."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from recallforge.config import Settings
from recallforge.embeddings.backfill import EmbeddingBackfillService
from recallforge.embeddings.registry import embedding_provider_from_settings
from recallforge.ingest.ingest_service import IngestService
from recallforge.ingest.ingest_service import embedding_provider_from_settings as ingest_embedding_config
from recallforge.retrieval.reranker.registry import reranker_provider_from_settings
from recallforge.retrieval.retrieval_service import RetrievalService
from recallforge.storage.pgvector_store import PgVectorStore

from .answering import HTTPAnswerGenerator
from .errors import ServiceUnavailableError
from .governance_service import GovernanceService
from .knowledge_service import KnowledgeService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_knowledge_service(request: Request) -> KnowledgeService:
    existing = getattr(request.app.state, "knowledge_service", None)
    if existing is not None:
        return existing
    factory = getattr(request.app.state, "knowledge_service_factory", None)
    if factory is not None:
        service = factory()
        request.app.state.knowledge_service = service
        return service

    settings: Settings = request.app.state.settings
    if not settings.api_enabled:
        raise ServiceUnavailableError("api_disabled", "RecallForge API is disabled")
    session_factory = _session_factory(request.app)
    embedding_provider = embedding_provider_from_settings(settings)
    ingest_service = IngestService(
        session_factory,
        settings,
        ingest_embedding_config(settings),
    )
    backfill_service = EmbeddingBackfillService(session_factory, embedding_provider, settings)

    @asynccontextmanager
    async def retrieval_provider():
        async with session_factory() as session:
            async with session.begin():
                reranker = reranker_provider_from_settings(settings)
                service = RetrievalService(
                    settings=settings,
                    embedding_provider=embedding_provider,
                    vector_store=PgVectorStore(session),
                    reranker=reranker,
                    session=session,
                )
                try:
                    yield service
                finally:
                    await service.close()

    service = KnowledgeService(
        settings=settings,
        ingest_service=ingest_service,
        backfill_service=backfill_service,
        retrieval_service_provider=retrieval_provider,
        session_factory=session_factory,
        answer_generator=HTTPAnswerGenerator(settings),
    )
    request.app.state.knowledge_service = service
    return service


def get_governance_service(request: Request) -> GovernanceService:
    existing = getattr(request.app.state, "governance_service", None)
    if existing is not None:
        return existing
    factory = getattr(request.app.state, "governance_service_factory", None)
    if factory is not None:
        service = factory()
        request.app.state.governance_service = service
        return service
    settings: Settings = request.app.state.settings
    if not settings.api_enabled:
        raise ServiceUnavailableError("api_disabled", "RecallForge API is disabled")
    session_factory = _session_factory(request.app)
    service = GovernanceService(
        settings=settings,
        session_factory=session_factory,
        knowledge_service=get_knowledge_service(request),
        vector_store_provider=lambda session: PgVectorStore(session),
    )
    request.app.state.governance_service = service
    return service


def _session_factory(app: Any):
    existing = getattr(app.state, "session_factory", None)
    if existing is not None:
        return existing
    settings: Settings = app.state.settings
    engine = create_async_engine(_async_database_url(settings.database_url), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app.state.engine = engine
    app.state.session_factory = factory
    return factory


def _async_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    return url
