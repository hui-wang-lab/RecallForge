"""Base types and helpers for template chunkers."""
from __future__ import annotations

from dataclasses import dataclass, field

from recallforge.chunking.ir.models import ChildChunk, ParentChunk, ParsedDocument


@dataclass(frozen=True)
class ChunkerConfig:
    child_max_tokens: int = 450
    child_min_tokens: int = 80
    parent_granularity: str = "chapter"
    table_context_blocks: int = 2
    image_context_blocks: int = 2


@dataclass
class ChunkingResult:
    chunker_used: str
    parent_chunks: list[ParentChunk] = field(default_factory=list)
    child_chunks: list[ChildChunk] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class TemplateChunker:
    name = "base"

    def chunk(self, document: ParsedDocument, config: ChunkerConfig) -> ChunkingResult:
        raise NotImplementedError

