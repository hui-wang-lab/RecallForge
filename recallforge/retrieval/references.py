"""Structured reference assembly."""

from __future__ import annotations

from collections import defaultdict
from typing import Sequence

from recallforge.retrieval.types import ExpandedCandidate, Reference, ReferenceChild


class ReferenceBuilder:
    def build(
        self,
        selected: Sequence[ExpandedCandidate],
        document_titles: dict[int, str | None] | None = None,
    ) -> list[Reference]:
        titles = document_titles or {}
        grouped: dict[int, list[ExpandedCandidate]] = defaultdict(list)
        for candidate in selected:
            grouped[candidate.parent_id].append(candidate)

        representatives = [
            sorted(items, key=lambda item: item.rerank_score, reverse=True)[0]
            for items in grouped.values()
        ]
        representatives.sort(key=lambda item: item.rerank_score, reverse=True)

        references: list[Reference] = []
        for index, candidate in enumerate(representatives, start=1):
            all_children = list(candidate.child_candidates) or []
            if not all_children:
                all_children = []
            child_refs = [
                ReferenceChild(
                    chunk_id=child.chunk_id,
                    chunk_key=child.chunk_key,
                    rerank_score=child.rerank_score,
                    rerank_rank=child.rerank_rank,
                    page_start=_int_or_none(child.metadata.get("page_start")),
                    page_end=_int_or_none(child.metadata.get("page_end")),
                )
                for child in sorted(all_children, key=lambda item: item.rerank_rank)
            ]
            if not child_refs:
                child_refs.append(
                    ReferenceChild(
                        chunk_id=candidate.chunk_id,
                        chunk_key=candidate.chunk_key,
                        rerank_score=candidate.rerank_score,
                        rerank_rank=candidate.rerank_rank,
                        page_start=candidate.page_start,
                        page_end=candidate.page_end,
                    )
                )
            references.append(
                Reference(
                    ref_id=f"[{index}]",
                    index=index,
                    document_id=candidate.document_id,
                    document_title=titles.get(candidate.document_id),
                    chunk_id=candidate.chunk_id,
                    chunk_key=candidate.chunk_key,
                    parent_id=candidate.parent_id,
                    parent_key=candidate.parent_key,
                    source_uri=candidate.source_uri,
                    doc_type=candidate.doc_type,
                    page_start=candidate.page_start,
                    page_end=candidate.page_end,
                    heading_path=candidate.heading_path,
                    version=candidate.version,
                    rerank_score=candidate.rerank_score,
                    vector_score=candidate.vector_score,
                    child_chunks=child_refs,
                )
            )
        return references


def _int_or_none(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None
