"""Small M6 stats helpers."""

from __future__ import annotations


def embedding_status(child_chunk_count: int) -> str:
    if child_chunk_count <= 0:
        return "missing"
    return "complete"
