"""Startup validation for M3 vector infrastructure."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from recallforge.config import Settings
from recallforge.embeddings.provider import EmbeddingConfigurationError, EmbeddingProvider
from recallforge.embeddings.registry import embedding_provider_from_settings
from recallforge.storage.embedding_columns import DEFAULT_EMBEDDING_COLUMNS, EmbeddingColumnRegistry

logger = logging.getLogger("recallforge.storage.vector_preflight")


async def validate_embedding_dimensions(
    settings: Settings,
    provider: EmbeddingProvider,
    columns: EmbeddingColumnRegistry = DEFAULT_EMBEDDING_COLUMNS,
    session: AsyncSession | None = None,
) -> None:
    if settings.embedding_model != provider.name:
        raise EmbeddingConfigurationError(
            f"settings.embedding_model={settings.embedding_model!r} does not match provider.name={provider.name!r}"
        )
    if settings.embedding_provider != provider.provider:
        raise EmbeddingConfigurationError(
            "settings.embedding_provider does not match provider.provider: "
            f"{settings.embedding_provider!r} != {provider.provider!r}"
        )
    if settings.embedding_dim != provider.dim:
        raise EmbeddingConfigurationError(
            f"settings.embedding_dim={settings.embedding_dim} does not match provider.dim={provider.dim}"
        )

    spec = columns.resolve(provider.name)
    if spec.provider != provider.provider:
        raise EmbeddingConfigurationError(
            f"column route provider mismatch for embedding_model={provider.name}: "
            f"expected {provider.provider}, got {spec.provider}"
        )
    if spec.dim != provider.dim:
        raise EmbeddingConfigurationError(
            f"column route dimension mismatch for embedding_model={provider.name}: "
            f"expected {provider.dim}, got {spec.dim}"
        )
    if spec.distance_metric != provider.distance_metric:
        raise EmbeddingConfigurationError(
            f"column route distance metric mismatch for embedding_model={provider.name}: "
            f"expected {provider.distance_metric}, got {spec.distance_metric}"
        )

    try:
        columns.validate_sqlalchemy_model(spec)
    except ValueError as exc:
        raise EmbeddingConfigurationError(str(exc)) from exc

    if session is not None:
        await _validate_live_database(session, spec, provider)


async def run_vector_startup_preflight(
    settings: Settings,
    session: AsyncSession,
    columns: EmbeddingColumnRegistry = DEFAULT_EMBEDDING_COLUMNS,
) -> None:
    provider = embedding_provider_from_settings(settings)
    try:
        spec = columns.resolve(settings.embedding_model)
        await provider.preflight()
        await validate_embedding_dimensions(settings, provider, columns, session)
        await _validate_embedding_metadata_shape(session)
        if not settings.reranker_model:
            logger.warning("reranker_model_not_configured_for_m4")
        logger.info(
            "vector_preflight_succeeded",
            extra={
                "provider": provider.provider,
                "embedding_model": provider.name,
                "embedding_dim": provider.dim,
                "column_name": spec.column_name,
                "distance_metric": spec.distance_metric,
                "region": getattr(provider, "region", ""),
            },
        )
    finally:
        close = getattr(provider, "close", None)
        if close is not None:
            await close()


async def _validate_live_database(session: AsyncSession, spec: Any, provider: EmbeddingProvider) -> None:
    type_result = await session.execute(
        text(
            """
            SELECT format_type(a.atttypid, a.atttypmod) AS type_name
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema()
              AND c.relname = 'rag_chunks'
              AND a.attname = :column_name
              AND a.attnum > 0
              AND NOT a.attisdropped
            """
        ),
        {"column_name": spec.column_name},
    )
    type_name = type_result.scalar_one_or_none()
    expected_type = f"vector({provider.dim})"
    if type_name != expected_type:
        raise EmbeddingConfigurationError(
            f"database vector column dimension mismatch for {spec.column_name}: "
            f"expected {expected_type}, got {type_name!r}"
        )

    mismatch_result = await session.execute(
        text(
            """
            SELECT count(*)
            FROM rag_chunks
            WHERE status = 'active'
              AND (
                embedding_provider <> :provider
                OR embedding_model <> :model
                OR embedding_dim <> :dim
              )
            """
        ),
        {"provider": provider.provider, "model": provider.name, "dim": provider.dim},
    )
    mismatch_count = mismatch_result.scalar_one()
    if mismatch_count:
        raise EmbeddingConfigurationError(
            "active rag_chunks contain embedding description mismatches: "
            f"count={mismatch_count}, expected provider={provider.provider}, "
            f"model={provider.name}, dim={provider.dim}"
        )


async def _validate_embedding_metadata_shape(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            """
            SELECT count(*)
            FROM rag_chunks
            WHERE embedding_metadata IS NOT NULL
              AND jsonb_typeof(embedding_metadata) <> 'object'
            """
        )
    )
    bad_count = result.scalar_one()
    if bad_count:
        raise EmbeddingConfigurationError(
            f"rag_chunks.embedding_metadata must be a JSON object; invalid row count={bad_count}"
        )
