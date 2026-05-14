from __future__ import annotations

from recallforge.config import Settings
from recallforge.retrieval.context_assembly import ContextAssembler
from recallforge.retrieval.references import ReferenceBuilder
from recallforge.retrieval.refusal import RefusalDecision
from recallforge.retrieval.types import ExpandedCandidate, RankedCandidate


def test_reference_builder_merges_children_under_same_parent():
    expanded = [
        _expanded(
            chunk_id=1,
            parent_id=10,
            child_candidates=[_ranked(1, 0.9), _ranked(2, 0.8)],
        ),
        _expanded(chunk_id=3, parent_id=11, score=0.7),
    ]

    refs = ReferenceBuilder().build(expanded, {100: "Handbook"})

    assert [ref.ref_id for ref in refs] == ["[1]", "[2]"]
    assert refs[0].document_title == "Handbook"
    assert refs[0].parent_id == 10
    assert [child.chunk_id for child in refs[0].child_chunks] == [1, 2]


def test_context_assembler_respects_budget_and_uses_references():
    settings = Settings(openai_api_key="test-key", max_context_tokens=80)
    assembler = ContextAssembler(settings)
    refusal = RefusalDecision(False, None, "high", 0.9, None, 1)
    expanded = [
        _expanded(chunk_id=1, parent_id=10, score=0.9, parent_content="A" * 100),
        _expanded(chunk_id=2, parent_id=11, score=0.8, parent_content="B" * 1000),
    ]

    assembled = assembler.assemble(expanded, refusal, {100: "Handbook"})

    assert assembled.candidates_included >= 1
    assert assembled.references[0].ref_id == "[1]"
    assert "核心段落" in assembled.context_text


def _ranked(chunk_id: int, score: float) -> RankedCandidate:
    return RankedCandidate(
        chunk_id=chunk_id,
        document_id=100,
        parent_id=10,
        chunk_key=f"c-{chunk_id}",
        parent_key="p-10",
        child_content=f"child {chunk_id}",
        rerank_score=score,
        rerank_rank=chunk_id,
        vector_score=score - 0.1,
        vector_rank=chunk_id,
        score_source="rerank",
        metadata={"page_start": chunk_id, "page_end": chunk_id},
    )


def _expanded(
    chunk_id: int,
    parent_id: int,
    score: float = 0.9,
    parent_content: str = "parent content",
    child_candidates: list[RankedCandidate] | None = None,
) -> ExpandedCandidate:
    return ExpandedCandidate(
        chunk_id=chunk_id,
        document_id=100,
        parent_id=parent_id,
        chunk_key=f"c-{chunk_id}",
        parent_key=f"p-{parent_id}",
        child_content=f"child {chunk_id}",
        parent_content=parent_content,
        parent_token_count=10,
        parent_truncated=False,
        heading_path=["Chapter"],
        page_start=1,
        page_end=2,
        source_uri="source.md",
        doc_type="markdown",
        version=1,
        rerank_score=score,
        rerank_rank=chunk_id,
        vector_score=score - 0.1,
        vector_rank=chunk_id,
        score_source="rerank",
        child_candidates=child_candidates or [_ranked(chunk_id, score)],
    )
