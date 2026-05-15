"""Shared retrieval dataclasses for RecallForge M4."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

RetrievalStatus = Literal["retrieved", "refused", "failed"]
SearchMode = Literal["vector", "hybrid", "full_text"]


@dataclass(frozen=True)
class RetrievalRequest:
    question: str
    client_filters: dict[str, Any] = field(default_factory=dict)
    top_k: int | None = None
    final_top_k: int | None = None
    search_mode: SearchMode = "vector"


@dataclass(frozen=True)
class SearchConfig:
    top_k: int
    final_top_k: int
    search_mode: str
    effective_search_mode: str
    min_rerank_score: float
    min_vector_score: float
    min_top1_margin: float
    max_context_tokens: int
    query_rewrite_enabled: bool
    hyde_enabled: bool


@dataclass(frozen=True)
class RankedCandidate:
    chunk_id: int
    document_id: int
    parent_id: int
    chunk_key: str
    parent_key: str
    child_content: str
    rerank_score: float
    rerank_rank: int
    vector_score: float
    vector_rank: int
    score_source: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExpandedCandidate:
    chunk_id: int
    document_id: int
    parent_id: int
    chunk_key: str
    parent_key: str
    child_content: str
    parent_content: str | None
    parent_token_count: int | None
    parent_truncated: bool
    heading_path: list[str] | None
    page_start: int | None
    page_end: int | None
    source_uri: str
    doc_type: str
    version: int
    rerank_score: float
    rerank_rank: int
    vector_score: float
    vector_rank: int
    score_source: str
    child_candidates: list[RankedCandidate] = field(default_factory=list)


@dataclass(frozen=True)
class ReferenceChild:
    chunk_id: int
    chunk_key: str
    rerank_score: float
    rerank_rank: int
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True)
class Reference:
    ref_id: str
    index: int
    document_id: int
    document_title: str | None
    chunk_id: int
    chunk_key: str
    parent_id: int
    parent_key: str
    source_uri: str
    doc_type: str
    page_start: int | None
    page_end: int | None
    heading_path: list[str] | None
    version: int
    rerank_score: float
    vector_score: float
    child_chunks: list[ReferenceChild]


@dataclass(frozen=True)
class HitSummary:
    chunk_id: int
    document_id: int
    parent_id: int
    chunk_key: str
    parent_key: str
    vector_rank: int
    vector_score: float
    rerank_rank: int | None = None
    rerank_score: float | None = None
    score_source: str = "vector"
    selected: bool = False
    content_snippet: str | None = None


@dataclass(frozen=True)
class AssembledContext:
    context_text: str
    total_tokens: int
    references: list[Reference]
    selected_candidates: list[ExpandedCandidate]
    truncation_applied: bool
    candidates_included: int
    candidates_dropped: int


@dataclass(frozen=True)
class RetrievalResult:
    status: RetrievalStatus
    context_text: str = ""
    references: list[Reference] = field(default_factory=list)
    hit_summary: list[HitSummary] = field(default_factory=list)
    refusal_reason: str | None = None
    error_message: str | None = None
    rewritten_query: str | None = None
    effective_query: str | None = None
    search_config: SearchConfig | None = None
    latencies_ms: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def dataclass_to_dict(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value
