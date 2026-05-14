"""Rule-based query understanding for the M4 retrieval pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from recallforge.config import Settings

_MEANINGLESS_PREFIXES = (
    "please",
    "can you",
    "could you",
    "tell me",
    "help me find",
    "help me search",
    "请问",
    "帮我查",
    "帮我找",
    "你知道",
)
_MULTI_INTENT_MARKERS = ("以及", "同时", "并且", "还有", " and ", " plus ", "；", ";")


@dataclass(frozen=True)
class QueryAnalysis:
    original_query: str
    effective_query: str
    rewritten_query: str | None = None
    rejected: bool = False
    rejection_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    multi_intent_detected: bool = False
    intent_count: int = 1


class QueryUnderstanding:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def analyze(self, question: str) -> QueryAnalysis:
        original = question or ""
        normalized = _squash_ws(original.strip())
        warnings: list[str] = []
        multi_intent, intent_count = _detect_multi_intent(normalized)

        if not normalized:
            return QueryAnalysis(
                original_query=original,
                effective_query="",
                rejected=True,
                rejection_reason="empty_query",
                warnings=warnings,
                multi_intent_detected=multi_intent,
                intent_count=intent_count,
            )
        if not _contains_meaningful_char(normalized):
            return QueryAnalysis(
                original_query=original,
                effective_query=normalized,
                rejected=True,
                rejection_reason="empty_query",
                warnings=["punctuation_only_query"],
                multi_intent_detected=multi_intent,
                intent_count=intent_count,
            )
        if len(normalized) < self._settings.min_query_length:
            return QueryAnalysis(
                original_query=original,
                effective_query=normalized,
                rejected=True,
                rejection_reason="query_too_short",
                warnings=warnings,
                multi_intent_detected=multi_intent,
                intent_count=intent_count,
            )

        effective = normalized
        rewritten: str | None = None
        if self._settings.query_rewrite_enabled:
            rewritten = _rewrite_query(normalized)
            effective = rewritten or normalized
            if rewritten != normalized:
                warnings.append("rule_based_query_rewrite_applied")
        if self._settings.hyde_enabled:
            warnings.append("hyde_enabled_but_not_implemented")

        return QueryAnalysis(
            original_query=original,
            effective_query=effective,
            rewritten_query=rewritten if rewritten != normalized else None,
            rejected=False,
            warnings=warnings,
            multi_intent_detected=multi_intent,
            intent_count=intent_count,
        )


def _squash_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _contains_meaningful_char(text: str) -> bool:
    return bool(re.search(r"[\w\u4e00-\u9fff]", text))


def _rewrite_query(text: str) -> str:
    lowered = text.lower()
    for prefix in _MEANINGLESS_PREFIXES:
        if lowered.startswith(prefix):
            return _squash_ws(text[len(prefix) :].lstrip(" ，,：:"))
    return text


def _detect_multi_intent(text: str) -> tuple[bool, int]:
    if not text:
        return False, 1
    intent_count = 1
    question_marks = len(re.findall(r"[?？]", text))
    if question_marks > 1:
        intent_count = max(intent_count, question_marks)
    for marker in _MULTI_INTENT_MARKERS:
        if marker in text:
            intent_count += text.count(marker)
    if "和" in text and len(text) > 8:
        intent_count += text.count("和")
    return intent_count > 1, intent_count
