"""Data models for ChunkFlow pipeline artifacts."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


def document_id_from_bytes(raw_bytes: bytes) -> str:
    """Deterministic document ID: sha256 of raw file bytes."""
    return hashlib.sha256(raw_bytes).hexdigest()


def chunk_key(document_id: str, page_number: int) -> str:
    return f"{document_id}:{page_number}"


def chunk_id_from_components(
    document_id: str,
    source_type: str,
    page_number: int,
    chunk_index: int,
) -> str:
    canonical = f"{document_id}:{source_type}:{page_number}:{chunk_index}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChunkingConfig:
    """Configuration for sliding-window chunking."""
    chunk_size_tokens: int = 400
    overlap_tokens: int = 100

    def __post_init__(self) -> None:
        if self.overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("overlap_tokens must be < chunk_size_tokens")
        if self.chunk_size_tokens < 50:
            raise ValueError("chunk_size_tokens must be >= 50")


@dataclass(frozen=True)
class Chunk:
    """One addressable unit of document content."""
    chunk_id: str
    chunk_key: str
    document_id: str
    source_type: str
    page_number: int
    chunk_index: int
    text: str
    chapter: str | None = None
    section: str | None = None
    domain_hint: str | None = None
    headings: list[str] = field(default_factory=list)
    content_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "chunk_id": self.chunk_id,
            "chunk_key": self.chunk_key,
            "document_id": self.document_id,
            "source_type": self.source_type,
            "page_number": self.page_number,
            "chunk_index": self.chunk_index,
            "text": self.text,
        }
        if self.chapter is not None:
            out["chapter"] = self.chapter
        if self.section is not None:
            out["section"] = self.section
        if self.domain_hint is not None:
            out["domain_hint"] = self.domain_hint
        if self.headings:
            out["headings"] = self.headings
        if self.content_type is not None:
            out["content_type"] = self.content_type
        return out


@dataclass
class Document:
    """Parsed document with ordered chunks."""
    document_id: str
    source_path: str
    chunks: list[Chunk] = field(default_factory=list)
    parser_used: str | None = None
    parser_fallback_chain: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "document_id": self.document_id,
            "source_path": self.source_path,
            "chunk_count": len(self.chunks),
            "chunks": [c.to_dict() for c in self.chunks],
        }
        if self.parser_used is not None:
            out["parser_used"] = self.parser_used
        if self.parser_fallback_chain:
            out["parser_fallback_chain"] = self.parser_fallback_chain
        return out
