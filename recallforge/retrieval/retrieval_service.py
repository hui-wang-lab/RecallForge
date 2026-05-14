"""M4 retrieval orchestration service."""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from recallforge.config import Settings
from recallforge.context import RequestContext
from recallforge.embeddings.provider import EmbeddingProvider
from recallforge.retrieval.context_assembly import ContextAssembler
from recallforge.retrieval.errors import FilterBuilderError, RerankerConfigurationError
from recallforge.retrieval.filter_builder import AuditLogHook, FilterBuilder
from recallforge.retrieval.parent_expansion import ParentExpander
from recallforge.retrieval.query_understanding import QueryAnalysis, QueryUnderstanding
from recallforge.retrieval.refusal import RefusalJudge
from recallforge.retrieval.reranker.provider import RerankedCandidate, RerankerInput, RerankerProvider
from recallforge.retrieval.types import (
    AssembledContext,
    HitSummary,
    RankedCandidate,
    RetrievalRequest,
    RetrievalResult,
    SearchConfig,
)
from recallforge.storage.repository import (
    ChunkRepository,
    DocumentRepository,
    ParentChunkRepository,
    QueryLogCreate,
    QueryLogRepository,
)
from recallforge.storage.vector_store import VectorSearchFilter, VectorSearchHit, VectorStoreAdapter


class RetrievalService:
    def __init__(
        self,
        settings: Settings,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStoreAdapter,
        reranker: RerankerProvider | None,
        session: AsyncSession,
        parent_repo_type: type[ParentChunkRepository] = ParentChunkRepository,
        chunk_repo_type: type[ChunkRepository] = ChunkRepository,
        query_log_repo_type: type[QueryLogRepository] = QueryLogRepository,
        doc_repo_type: type[DocumentRepository] = DocumentRepository,
        audit_hook: AuditLogHook | None = None,
    ) -> None:
        if settings.reranker_required and reranker is None:
            raise RerankerConfigurationError(
                "reranker_required=True but reranker_model is empty; configure reranker_model "
                "or set reranker_required=False for testing"
            )
        self._settings = settings
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._reranker = reranker
        self._session = session
        self._parent_repo = parent_repo_type(session)
        self._chunk_repo = chunk_repo_type(session)
        self._query_log_repo = query_log_repo_type(session)
        self._doc_repo = doc_repo_type(session)
        self._query_understanding = QueryUnderstanding(settings)
        self._filter_builder = FilterBuilder(settings, audit_hook=audit_hook)
        self._refusal_judge = RefusalJudge(settings)
        self._context_assembler = ContextAssembler(settings)

    async def close(self) -> None:
        if self._reranker is not None and hasattr(self._reranker, "close"):
            await self._reranker.close()

    async def retrieve(self, request: RetrievalRequest, ctx: RequestContext) -> RetrievalResult:
        started = time.perf_counter()
        latencies: dict[str, int] = {}
        warnings: list[str] = []
        metadata: dict[str, Any] = {}
        filters: VectorSearchFilter | None = None
        analysis: QueryAnalysis | None = None
        hit_summary: list[HitSummary] = []
        config = self._search_config(request)
        effective_search_mode = config.effective_search_mode

        try:
            with _timed(latencies, "query_understanding_ms"):
                analysis = self._query_understanding.analyze(request.question)
            warnings.extend(analysis.warnings)
            metadata.update(
                {
                    "warnings": warnings,
                    "multi_intent_detected": analysis.multi_intent_detected,
                    "intent_count": analysis.intent_count,
                    "search_config": asdict(config),
                }
            )
            if request.search_mode == "hybrid":
                warnings.append("hybrid_search_downgraded_to_vector")
            if request.search_mode == "full_text":
                warnings.append("full_text_search_not_implemented_downgraded_to_vector")

            if analysis.rejected:
                result = RetrievalResult(
                    status="refused",
                    refusal_reason=analysis.rejection_reason,
                    rewritten_query=analysis.rewritten_query,
                    effective_query=analysis.effective_query,
                    search_config=config,
                    latencies_ms=_finish_latencies(latencies, started),
                    metadata=metadata,
                )
                await self._write_log(request, ctx, result, filters, analysis)
                return result

            with _timed(latencies, "filter_build_ms"):
                filters = self._filter_builder.build(ctx, request.client_filters)

            with _timed(latencies, "embedding_ms"):
                query_embedding = await self._embedding_provider.embed_query(analysis.effective_query)

            with _timed(latencies, "vector_search_ms"):
                vector_hits = await self._vector_store.search(
                    query_embedding=query_embedding,
                    embedding_model=self._embedding_provider.name,
                    filters=filters,
                    top_k=config.top_k,
                    search_mode=effective_search_mode,
                )
            hit_summary = [_summary_from_hit(hit) for hit in vector_hits]

            with _timed(latencies, "chunk_read_ms"):
                chunks = await self._chunk_repo.get_by_ids(ctx.tenant_id, [hit.chunk_id for hit in vector_hits])
            chunks_by_id = {chunk.id: chunk for chunk in chunks}
            vector_hits = [hit for hit in vector_hits if hit.chunk_id in chunks_by_id]

            ranked, score_source = await self._rerank_or_fallback(
                analysis.effective_query,
                vector_hits,
                chunks_by_id,
                config.final_top_k,
                latencies,
                warnings,
                metadata,
            )
            hit_summary = _merge_rerank_summary(hit_summary, ranked)

            refusal = self._refusal_judge.judge(ranked, score_source=score_source)
            metadata["refusal_decision"] = asdict(refusal)
            if refusal.confidence == "medium":
                warnings.append("low_top1_margin")

            if refusal.should_refuse:
                result = RetrievalResult(
                    status="refused",
                    hit_summary=hit_summary,
                    refusal_reason=refusal.reason,
                    rewritten_query=analysis.rewritten_query,
                    effective_query=analysis.effective_query,
                    search_config=config,
                    latencies_ms=_finish_latencies(latencies, started),
                    metadata=metadata,
                )
                await self._write_log(request, ctx, result, filters, analysis)
                return result

            with _timed(latencies, "parent_expansion_ms"):
                expanded = await ParentExpander(
                    self._parent_repo,
                    self._chunk_repo,
                    self._settings,
                    warnings,
                ).expand(ranked, ctx.tenant_id)

            with _timed(latencies, "doc_title_read_ms"):
                docs = await self._doc_repo.get_by_ids(ctx.tenant_id, sorted({item.document_id for item in expanded}))
            document_titles = {doc.id: doc.title for doc in docs}

            with _timed(latencies, "context_assembly_ms"):
                assembled = self._context_assembler.assemble(expanded, refusal, document_titles)
            if not assembled.selected_candidates:
                result = RetrievalResult(
                    status="refused",
                    hit_summary=hit_summary,
                    refusal_reason="context_budget_exceeded",
                    rewritten_query=analysis.rewritten_query,
                    effective_query=analysis.effective_query,
                    search_config=config,
                    latencies_ms=_finish_latencies(latencies, started),
                    metadata=metadata,
                )
                await self._write_log(request, ctx, result, filters, analysis)
                return result

            selected_ids = {candidate.chunk_id for candidate in assembled.selected_candidates}
            hit_summary = [
                HitSummary(**{**asdict(summary), "selected": summary.chunk_id in selected_ids})
                for summary in hit_summary
            ]
            metadata["context"] = {
                "total_tokens": assembled.total_tokens,
                "truncation_applied": assembled.truncation_applied,
                "candidates_included": assembled.candidates_included,
                "candidates_dropped": assembled.candidates_dropped,
            }
            result = RetrievalResult(
                status="retrieved",
                context_text=assembled.context_text,
                references=assembled.references,
                hit_summary=hit_summary,
                rewritten_query=analysis.rewritten_query,
                effective_query=analysis.effective_query,
                search_config=config,
                latencies_ms=_finish_latencies(latencies, started),
                metadata=metadata,
            )
            await self._write_log(request, ctx, result, filters, analysis, assembled)
            return result
        except FilterBuilderError as exc:
            result = RetrievalResult(
                status="failed",
                error_message=str(exc),
                rewritten_query=analysis.rewritten_query if analysis else None,
                effective_query=analysis.effective_query if analysis else None,
                search_config=config,
                latencies_ms=_finish_latencies(latencies, started),
                metadata=metadata,
            )
            await self._write_log(request, ctx, result, filters, analysis)
            return result
        except Exception as exc:
            result = RetrievalResult(
                status="failed",
                error_message=_safe_error_message(exc),
                hit_summary=hit_summary,
                rewritten_query=analysis.rewritten_query if analysis else None,
                effective_query=analysis.effective_query if analysis else None,
                search_config=config,
                latencies_ms=_finish_latencies(latencies, started),
                metadata=metadata,
            )
            await self._write_log(request, ctx, result, filters, analysis)
            return result

    async def _rerank_or_fallback(
        self,
        query: str,
        hits: Sequence[VectorSearchHit],
        chunks_by_id: dict[int, Any],
        final_top_k: int,
        latencies: dict[str, int],
        warnings: list[str],
        metadata: dict[str, Any],
    ) -> tuple[list[RankedCandidate], str]:
        if not hits:
            return [], "rerank" if self._reranker else "vector"

        if self._reranker is None:
            return _rank_from_vector(hits, chunks_by_id, final_top_k), "vector"

        try:
            with _timed(latencies, "rerank_ms"):
                reranked = await self._reranker.rerank(
                    query,
                    [
                        RerankerInput(
                            chunk_id=hit.chunk_id,
                            content=chunks_by_id[hit.chunk_id].content,
                            original_rank=hit.rank,
                            original_score=hit.score,
                        )
                        for hit in hits
                    ],
                    top_k=final_top_k,
                )
            return _rank_from_rerank(reranked, hits, chunks_by_id), "rerank"
        except Exception as exc:
            warnings.append("reranker_fallback")
            metadata["reranker_fallback"] = True
            metadata["reranker_error"] = _safe_error_message(exc)
            return _rank_from_vector(hits, chunks_by_id, final_top_k), "vector"

    def _search_config(self, request: RetrievalRequest) -> SearchConfig:
        top_k = request.top_k or self._settings.default_top_k
        final_top_k = request.final_top_k or self._settings.final_top_k
        effective_mode = "vector" if request.search_mode in {"hybrid", "full_text"} else request.search_mode
        return SearchConfig(
            top_k=top_k,
            final_top_k=final_top_k,
            search_mode=request.search_mode,
            effective_search_mode=effective_mode,
            min_rerank_score=self._settings.min_rerank_score,
            min_vector_score=self._settings.min_vector_score,
            min_top1_margin=self._settings.min_top1_margin,
            max_context_tokens=self._settings.max_context_tokens,
            query_rewrite_enabled=self._settings.query_rewrite_enabled,
            hyde_enabled=self._settings.hyde_enabled,
        )

    async def _write_log(
        self,
        request: RetrievalRequest,
        ctx: RequestContext,
        result: RetrievalResult,
        filters: VectorSearchFilter | None,
        analysis: QueryAnalysis | None,
        assembled: AssembledContext | None = None,
    ) -> None:
        config = result.search_config or self._search_config(request)
        await self._query_log_repo.create(
            QueryLogCreate(
                request_id=ctx.request_id,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                department=ctx.department,
                access_level=ctx.access_level,
                question=request.question,
                rewritten_query=result.rewritten_query,
                filters=asdict(filters) if filters else {},
                client_filters=request.client_filters,
                search_mode=config.search_mode,
                embedding_provider=self._embedding_provider.provider,
                embedding_model=self._embedding_provider.name,
                embedding_dim=self._embedding_provider.dim,
                reranker_provider=self._reranker.provider if self._reranker else None,
                reranker_model=self._reranker.name if self._reranker else None,
                top_k=config.top_k,
                final_top_k=config.final_top_k,
                min_rerank_score=config.min_rerank_score,
                min_top1_margin=config.min_top1_margin,
                max_context_tokens=config.max_context_tokens,
                hit_summary=[asdict(item) for item in result.hit_summary],
                selected_references=[asdict(item) for item in result.references],
                answer=None,
                refusal_reason=result.refusal_reason,
                latencies_ms=result.latencies_ms,
                metadata=result.metadata,
                status=result.status,
                error_message=result.error_message,
            )
        )


def _rank_from_vector(
    hits: Sequence[VectorSearchHit],
    chunks_by_id: dict[int, Any],
    final_top_k: int,
) -> list[RankedCandidate]:
    ranked: list[RankedCandidate] = []
    for rank, hit in enumerate(sorted(hits, key=lambda item: item.score, reverse=True)[:final_top_k], start=1):
        chunk = chunks_by_id[hit.chunk_id]
        ranked.append(_ranked_candidate(hit, chunk.content, hit.score, rank, "vector"))
    return ranked


def _rank_from_rerank(
    reranked: Sequence[RerankedCandidate],
    hits: Sequence[VectorSearchHit],
    chunks_by_id: dict[int, Any],
) -> list[RankedCandidate]:
    hits_by_id = {hit.chunk_id: hit for hit in hits}
    ranked: list[RankedCandidate] = []
    for item in reranked:
        hit = hits_by_id.get(item.chunk_id)
        if hit is None or item.chunk_id not in chunks_by_id:
            continue
        ranked.append(
            _ranked_candidate(
                hit,
                chunks_by_id[item.chunk_id].content,
                item.rerank_score,
                item.rerank_rank,
                "rerank",
            )
        )
    return sorted(ranked, key=lambda item: item.rerank_score, reverse=True)


def _ranked_candidate(
    hit: VectorSearchHit,
    content: str,
    score: float,
    rank: int,
    score_source: str,
) -> RankedCandidate:
    return RankedCandidate(
        chunk_id=hit.chunk_id,
        document_id=hit.document_id,
        parent_id=hit.parent_id,
        chunk_key=hit.chunk_key,
        parent_key=hit.parent_key,
        child_content=content,
        rerank_score=score,
        rerank_rank=rank,
        vector_score=hit.score,
        vector_rank=hit.rank,
        score_source=score_source,
        metadata=hit.metadata,
    )


def _summary_from_hit(hit: VectorSearchHit) -> HitSummary:
    return HitSummary(
        chunk_id=hit.chunk_id,
        document_id=hit.document_id,
        parent_id=hit.parent_id,
        chunk_key=hit.chunk_key,
        parent_key=hit.parent_key,
        vector_rank=hit.rank,
        vector_score=hit.score,
        score_source=hit.score_source,
    )


def _merge_rerank_summary(summaries: Sequence[HitSummary], ranked: Sequence[RankedCandidate]) -> list[HitSummary]:
    reranked_by_id = {item.chunk_id: item for item in ranked}
    merged: list[HitSummary] = []
    for summary in summaries:
        ranked_item = reranked_by_id.get(summary.chunk_id)
        data = asdict(summary)
        if ranked_item is not None:
            data.update(
                {
                    "rerank_rank": ranked_item.rerank_rank,
                    "rerank_score": ranked_item.rerank_score,
                    "score_source": ranked_item.score_source,
                }
            )
        merged.append(HitSummary(**data))
    return merged


class _timed:
    def __init__(self, latencies: dict[str, int], key: str) -> None:
        self._latencies = latencies
        self._key = key
        self._started = 0.0

    def __enter__(self) -> None:
        self._started = time.perf_counter()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._latencies[self._key] = int((time.perf_counter() - self._started) * 1000)


def _finish_latencies(latencies: dict[str, int], started: float) -> dict[str, int]:
    finished = dict(latencies)
    finished["total_ms"] = int((time.perf_counter() - started) * 1000)
    return finished


def _safe_error_message(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {str(exc)[:300]}"
