"""Context assembly with stable reference numbering."""

from __future__ import annotations

from typing import Sequence

from recallforge.chunking.tokenizer import estimate_tokens
from recallforge.config import Settings
from recallforge.retrieval.references import ReferenceBuilder
from recallforge.retrieval.refusal import RefusalDecision
from recallforge.retrieval.types import AssembledContext, ExpandedCandidate


class ContextAssembler:
    def __init__(self, settings: Settings, reference_builder: ReferenceBuilder | None = None) -> None:
        self._settings = settings
        self._reference_builder = reference_builder or ReferenceBuilder()

    def assemble(
        self,
        expanded: Sequence[ExpandedCandidate],
        refusal: RefusalDecision,
        document_titles: dict[int, str | None] | None = None,
    ) -> AssembledContext:
        if not expanded or refusal.should_refuse:
            return AssembledContext("", 0, [], [], False, 0, len(expanded))

        sorted_candidates = sorted(expanded, key=lambda item: item.rerank_score, reverse=True)
        selected: list[ExpandedCandidate] = []
        total_tokens = 0
        truncation_applied = False

        for candidate in sorted_candidates:
            ref_id = self._placeholder_ref_id(len(selected) + 1)
            block = _format_block(candidate, ref_id)
            tokens = estimate_tokens(block)
            if total_tokens + tokens > self._settings.max_context_tokens:
                truncation_applied = True
                compact = _format_block(candidate, ref_id, compact=True)
                compact_tokens = estimate_tokens(compact)
                if total_tokens + compact_tokens > self._settings.max_context_tokens:
                    continue
                block = compact
                tokens = compact_tokens
            selected.append(candidate)
            total_tokens += tokens

        references = self._reference_builder.build(selected, document_titles)
        ref_id_by_parent = {ref.parent_id: ref.ref_id for ref in references}
        blocks = [
            _format_block(c, ref_id_by_parent.get(c.parent_id, self._placeholder_ref_id(i + 1)))
            for i, c in enumerate(selected)
        ]
        return AssembledContext(
            context_text="\n\n---\n\n".join(blocks),
            total_tokens=total_tokens,
            references=references,
            selected_candidates=selected,
            truncation_applied=truncation_applied,
            candidates_included=len(selected),
            candidates_dropped=max(len(expanded) - len(selected), 0),
        )

    @staticmethod
    def _placeholder_ref_id(index: int) -> str:
        return f"[{index}]"


def _format_block(candidate: ExpandedCandidate, ref_id: str, *, compact: bool = False) -> str:
    page = _page_label(candidate.page_start, candidate.page_end)
    heading = " / ".join(candidate.heading_path or [])
    parent = "" if compact else (candidate.parent_content or "")
    parts = [
        ref_id,
        f"来源: {candidate.source_uri} | 页码: {page} | 类型: {candidate.doc_type}",
    ]
    if heading:
        parts.append(f"标题路径: {heading}")
    if parent:
        parts.append(parent)
    parts.extend(["核心段落:", candidate.child_content])
    return "\n".join(parts)


def _page_label(start: int | None, end: int | None) -> str:
    if start is None and end is None:
        return "未知"
    if start == end or end is None:
        return str(start)
    if start is None:
        return str(end)
    return f"{start}-{end}"
