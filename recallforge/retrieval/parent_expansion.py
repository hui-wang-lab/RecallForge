"""Parent chunk lookup and small-to-big context expansion."""

from __future__ import annotations

from collections import defaultdict
from typing import Sequence

from recallforge.chunking.tokenizer import estimate_tokens
from recallforge.config import Settings
from recallforge.retrieval.types import ExpandedCandidate, RankedCandidate
from recallforge.storage.repository import ChunkRepository, ParentChunkRecord, ParentChunkRepository


class ParentExpander:
    def __init__(
        self,
        parent_repo: ParentChunkRepository,
        chunk_repo: ChunkRepository,
        settings: Settings,
        warnings: list[str] | None = None,
    ) -> None:
        self._parent_repo = parent_repo
        self._chunk_repo = chunk_repo
        self._settings = settings
        self._warnings = warnings if warnings is not None else []

    async def expand(self, candidates: Sequence[RankedCandidate], tenant_id: str) -> list[ExpandedCandidate]:
        if not candidates:
            return []

        by_parent: dict[int, list[RankedCandidate]] = defaultdict(list)
        for candidate in candidates:
            by_parent[candidate.parent_id].append(candidate)

        parent_ids = list(by_parent)
        parents = await self._parent_repo.get_by_ids(tenant_id, parent_ids)
        parents_by_id = {parent.id: parent for parent in parents}

        expanded: list[ExpandedCandidate] = []
        for parent_id, grouped in by_parent.items():
            grouped_sorted = sorted(grouped, key=lambda item: item.rerank_score, reverse=True)
            representative = grouped_sorted[0]
            parent = parents_by_id.get(parent_id)
            if parent is None:
                self._warnings.append(f"parent_missing:{parent_id}")
            parent_content, truncated = self._parent_content(parent, grouped_sorted)
            metadata = representative.metadata
            expanded.append(
                ExpandedCandidate(
                    chunk_id=representative.chunk_id,
                    document_id=representative.document_id,
                    parent_id=representative.parent_id,
                    chunk_key=representative.chunk_key,
                    parent_key=representative.parent_key,
                    child_content=representative.child_content,
                    parent_content=parent_content,
                    parent_token_count=parent.token_count if parent else None,
                    parent_truncated=truncated,
                    heading_path=parent.heading_path if parent else _list_or_none(metadata.get("heading_path")),
                    page_start=parent.page_start if parent else _int_or_none(metadata.get("page_start")),
                    page_end=parent.page_end if parent else _int_or_none(metadata.get("page_end")),
                    source_uri=parent.source_uri if parent else str(metadata.get("source_uri", "")),
                    doc_type=parent.doc_type if parent else str(metadata.get("doc_type", "")),
                    version=parent.version if parent else int(metadata.get("version", 1)),
                    rerank_score=representative.rerank_score,
                    rerank_rank=representative.rerank_rank,
                    vector_score=representative.vector_score,
                    vector_rank=representative.vector_rank,
                    score_source=representative.score_source,
                    child_candidates=grouped_sorted,
                )
            )
        return sorted(expanded, key=lambda item: item.rerank_score, reverse=True)

    def _parent_content(
        self,
        parent: ParentChunkRecord | None,
        children: Sequence[RankedCandidate],
    ) -> tuple[str | None, bool]:
        if parent is None:
            return None, False
        content = parent.content
        token_count = parent.token_count if parent.token_count is not None else estimate_tokens(content)
        if token_count <= self._settings.parent_context_window_tokens * 2:
            return content, False
        child_text = children[0].child_content
        position = content.find(child_text)
        if position < 0:
            ratio = max(children[0].vector_rank - 1, 0) / max(len(children), 1)
            position = int(len(content) * min(ratio, 0.9))
        chars_per_token = max(len(content) / max(token_count, 1), 1.0)
        window_chars = int(self._settings.parent_context_window_tokens * chars_per_token)
        start = max(position - window_chars, 0)
        end = min(position + len(child_text) + window_chars, len(content))
        snippet = content[start:end].strip()
        if start > 0:
            snippet = "[...] " + snippet
        if end < len(content):
            snippet = snippet + " [...]"
        return snippet, True


def _list_or_none(value: object) -> list[str] | None:
    if isinstance(value, list):
        return [str(item) for item in value]
    return None


def _int_or_none(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None
