"""Base parser adapter types."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from recallforge.chunking.ir.models import ParsedDocument


@dataclass(frozen=True)
class ParserConfig:
    max_tokens: int = 400
    chunk_size_tokens: int = 400
    overlap_tokens: int = 100
    min_chunk_tokens: int = 50


class ParserAdapter:
    name = "base"

    def is_available(self) -> bool:
        return True

    def parse(self, path: str | Path, config: ParserConfig) -> ParsedDocument:
        raise NotImplementedError

