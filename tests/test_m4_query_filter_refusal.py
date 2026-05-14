from __future__ import annotations

import uuid

import pytest

from recallforge.config import Settings
from recallforge.context import RequestContext
from recallforge.retrieval.errors import FilterBuilderError
from recallforge.retrieval.filter_builder import FilterBuilder
from recallforge.retrieval.query_understanding import QueryUnderstanding
from recallforge.retrieval.refusal import RefusalJudge
from recallforge.retrieval.types import RankedCandidate


def _settings(**overrides) -> Settings:
    data = {"openai_api_key": "test-key"}
    data.update(overrides)
    return Settings(**data)


def _ctx(**overrides) -> RequestContext:
    data = dict(
        tenant_id="tenant-a",
        user_id="user-1",
        department="engineering",
        access_level="internal",
        request_id=uuid.uuid4(),
    )
    data.update(overrides)
    return RequestContext(**data)


def test_query_understanding_rejects_empty_and_short_queries():
    qu = QueryUnderstanding(_settings(min_query_length=2))

    assert qu.analyze("   ").rejection_reason == "empty_query"
    assert qu.analyze("?!!").rejection_reason == "empty_query"
    assert qu.analyze("a").rejection_reason == "query_too_short"


def test_query_understanding_detects_multi_intent_and_rule_rewrite():
    analysis = QueryUnderstanding(_settings(query_rewrite_enabled=True)).analyze("请问 退款政策以及发票规则？")

    assert analysis.rejected is False
    assert analysis.multi_intent_detected is True
    assert analysis.intent_count >= 2
    assert analysis.effective_query == "退款政策以及发票规则？"
    assert analysis.rewritten_query == "退款政策以及发票规则？"


def test_filter_builder_rejects_forbidden_and_unknown_client_filters():
    events = []
    builder = FilterBuilder(_settings(), audit_hook=lambda event, payload: events.append((event, payload)))

    with pytest.raises(FilterBuilderError):
        builder.build(_ctx(), {"tenant_id": "*"})
    with pytest.raises(FilterBuilderError):
        builder.build(_ctx(), {"evil": "x"})

    assert events[0][0] == "client_filter_forbidden"
    assert events[1][0] == "client_filter_unknown"


def test_filter_builder_expands_department_and_access_level():
    vector_filter = FilterBuilder(_settings()).build(_ctx(access_level="restricted"), {"doc_type": "pdf", "version": 3})

    assert vector_filter.tenant_id == "tenant-a"
    assert vector_filter.department == ["engineering", "global"]
    assert vector_filter.access_level == ["public", "internal", "confidential", "restricted"]
    assert vector_filter.status == "active"
    assert vector_filter.doc_type == "pdf"
    assert vector_filter.version == 3


def test_refusal_judge_uses_rerank_and_vector_thresholds():
    settings = _settings(min_rerank_score=0.35, min_vector_score=0.6, min_top1_margin=0.05)
    judge = RefusalJudge(settings)
    low_rerank = [_candidate(score=0.2), _candidate(chunk_id=2, score=0.1)]
    low_vector = [_candidate(score=0.55)]
    medium = [_candidate(score=0.8), _candidate(chunk_id=2, score=0.77)]

    assert judge.judge([], score_source="rerank").reason == "no_candidates"
    assert judge.judge(low_rerank, score_source="rerank").reason == "low_confidence"
    assert judge.judge(low_vector, score_source="vector").reason == "low_confidence"
    assert judge.judge(medium, score_source="rerank").confidence == "medium"
    assert judge.judge([_candidate(score=0.8)], score_source="rerank").confidence == "high"


def _candidate(chunk_id: int = 1, score: float = 0.8) -> RankedCandidate:
    return RankedCandidate(
        chunk_id=chunk_id,
        document_id=10,
        parent_id=20,
        chunk_key=f"c-{chunk_id}",
        parent_key="p-1",
        child_content="content",
        rerank_score=score,
        rerank_rank=chunk_id,
        vector_score=0.7,
        vector_rank=chunk_id,
        score_source="rerank",
    )
