from __future__ import annotations

from recallforge.api.answering import REFUSAL_ANSWER, build_answer_prompt, validate_answer_citations
from recallforge.retrieval.types import Reference, ReferenceChild


def test_answer_prompt_constrains_context_and_references():
    prompt = build_answer_prompt("What is covered?", "Context [1]", [_reference(1)])

    assert "Use only the provided context" in prompt
    assert "Do not invent reference numbers" in prompt
    assert "[1]" in prompt
    assert "当前资料无法确认" in prompt


def test_valid_citations_pass_and_invented_citations_fail():
    refs = [_reference(1), _reference(2)]

    valid = validate_answer_citations("The policy is covered [1][2].", refs)
    invented = validate_answer_citations("The policy is covered [9].", refs)
    missing = validate_answer_citations("The policy is covered.", refs)

    assert valid.valid is True
    assert invented.valid is False
    assert invented.reason == "invented citation: 9"
    assert missing.valid is False


def test_refusal_answer_can_omit_citations():
    result = validate_answer_citations(REFUSAL_ANSWER, [_reference(1)])

    assert result.valid is True
    assert result.citations == []


def _reference(index: int) -> Reference:
    return Reference(
        ref_id=f"[{index}]",
        index=index,
        document_id=100,
        document_title="Handbook",
        chunk_id=index,
        chunk_key=f"c-{index}",
        parent_id=200,
        parent_key="p-200",
        source_uri="handbook.md",
        doc_type="markdown",
        page_start=1,
        page_end=2,
        heading_path=["Handbook"],
        version=1,
        rerank_score=0.9,
        vector_score=0.8,
        child_chunks=[
            ReferenceChild(
                chunk_id=index,
                chunk_key=f"c-{index}",
                rerank_score=0.9,
                rerank_rank=index,
                page_start=1,
                page_end=2,
            )
        ],
    )
