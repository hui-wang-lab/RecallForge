"""Evidence sufficiency checks for retrieval results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from recallforge.config import Settings
from recallforge.retrieval.types import RankedCandidate


@dataclass(frozen=True)
class RefusalDecision:
    should_refuse: bool
    reason: str | None
    confidence: str
    top1_score: float | None
    top1_margin: float | None
    candidates_above_threshold: int


class RefusalJudge:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def judge(self, candidates: Sequence[RankedCandidate], score_source: str = "rerank") -> RefusalDecision:
        if not candidates:
            return RefusalDecision(True, "no_candidates", "none", None, None, 0)

        threshold = self._settings.min_vector_score if score_source == "vector" else self._settings.min_rerank_score
        scores = [candidate.rerank_score for candidate in candidates]
        top1_score = scores[0]
        top1_margin = top1_score - scores[1] if len(scores) > 1 else None
        above = sum(1 for score in scores if score >= threshold)

        if top1_score < threshold:
            return RefusalDecision(True, "low_confidence", "low", top1_score, top1_margin, above)
        if top1_margin is not None and top1_margin < self._settings.min_top1_margin:
            return RefusalDecision(False, None, "medium", top1_score, top1_margin, above)
        return RefusalDecision(False, None, "high", top1_score, top1_margin, above)
