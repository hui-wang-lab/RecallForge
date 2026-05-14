"""Reranker provider contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class RerankerInput:
    chunk_id: int
    content: str
    original_rank: int
    original_score: float


@dataclass(frozen=True)
class RerankedCandidate:
    chunk_id: int
    rerank_score: float
    rerank_rank: int
    original_rank: int
    original_score: float


class RerankerProvider(Protocol):
    provider: str
    name: str
    max_candidates: int

    async def rerank(
        self,
        query: str,
        candidates: Sequence[RerankerInput],
        top_k: int | None = None,
    ) -> list[RerankedCandidate]:
        """Rerank child chunk candidates."""

    async def preflight(self) -> None:
        """Validate provider configuration and remote availability."""
