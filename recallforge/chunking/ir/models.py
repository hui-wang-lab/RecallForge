"""Document IR and chunk package models."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_none(v) for v in value]
    return value


@dataclass
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    def to_dict(self) -> dict[str, float]:
        return {
            "x0": self.x0,
            "y0": self.y0,
            "x1": self.x1,
            "y1": self.y1,
        }


@dataclass
class BBoxRef:
    block_id: str
    page_number: int
    bbox: BBox | None = None

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "block_id": self.block_id,
                "page_number": self.page_number,
                "bbox": self.bbox.to_dict() if self.bbox else None,
            }
        )


@dataclass
class Page:
    page_number: int
    width: float | None = None
    height: float | None = None
    rotation: int | None = None
    block_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "page_number": self.page_number,
                "width": self.width,
                "height": self.height,
                "rotation": self.rotation,
                "block_ids": list(self.block_ids),
            }
        )


@dataclass
class Block:
    block_id: str
    document_id: str
    page_number: int
    block_type: str
    text: str
    html: str | None = None
    markdown: str | None = None
    bbox: BBox | None = None
    reading_order: int = 0
    heading_path: list[str] = field(default_factory=list)
    section_id: str | None = None
    caption: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "block_id": self.block_id,
                "document_id": self.document_id,
                "page_number": self.page_number,
                "block_type": self.block_type,
                "text": self.text,
                "html": self.html,
                "markdown": self.markdown,
                "bbox": self.bbox.to_dict() if self.bbox else None,
                "reading_order": self.reading_order,
                "heading_path": list(self.heading_path),
                "section_id": self.section_id,
                "caption": self.caption,
                "confidence": self.confidence,
                "metadata": dict(self.metadata),
            }
        )


@dataclass
class SectionNode:
    section_id: str
    parent_section_id: str | None
    title: str
    level: int
    page_start: int
    page_end: int
    block_ids: list[str] = field(default_factory=list)
    heading_path: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "section_id": self.section_id,
                "parent_section_id": self.parent_section_id,
                "title": self.title,
                "level": self.level,
                "page_start": self.page_start,
                "page_end": self.page_end,
                "block_ids": list(self.block_ids),
                "heading_path": list(self.heading_path),
            }
        )


@dataclass
class ParseReport:
    page_count: int = 0
    block_count: int = 0
    table_count: int = 0
    figure_count: int = 0
    parser_used: str = ""
    parser_fallback_chain: list[str] = field(default_factory=list)
    parent_chunk_count: int = 0
    child_chunk_count: int = 0
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_count": self.page_count,
            "block_count": self.block_count,
            "table_count": self.table_count,
            "figure_count": self.figure_count,
            "parser_used": self.parser_used,
            "parser_fallback_chain": list(self.parser_fallback_chain),
            "parent_chunk_count": self.parent_chunk_count,
            "child_chunk_count": self.child_chunk_count,
            "warnings": list(self.warnings),
            "metrics": dict(self.metrics),
        }


@dataclass
class ParsedDocument:
    document_id: str
    source_path: str
    filename: str
    file_type: str
    document_type: str | None
    parser_used: str
    parser_fallback_chain: list[str] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    section_tree: list[SectionNode] = field(default_factory=list)
    parse_report: ParseReport = field(default_factory=ParseReport)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "document_id": self.document_id,
                "source_path": self.source_path,
                "filename": self.filename,
                "file_type": self.file_type,
                "document_type": self.document_type,
                "parser_used": self.parser_used,
                "parser_fallback_chain": list(self.parser_fallback_chain),
                "pages": [p.to_dict() for p in self.pages],
                "blocks": [b.to_dict() for b in self.blocks],
                "section_tree": [s.to_dict() for s in self.section_tree],
                "parse_report": self.parse_report.to_dict(),
                "metadata": dict(self.metadata),
            }
        )


@dataclass
class ParentChunk:
    parent_id: str
    document_id: str
    section_id: str
    heading_path: list[str]
    title: str
    text: str
    page_span: tuple[int, int]
    source_block_ids: list[str]
    child_chunk_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parent_id": self.parent_id,
            "document_id": self.document_id,
            "section_id": self.section_id,
            "heading_path": list(self.heading_path),
            "title": self.title,
            "text": self.text,
            "page_span": list(self.page_span),
            "source_block_ids": list(self.source_block_ids),
            "child_chunk_ids": list(self.child_chunk_ids),
            "metadata": dict(self.metadata),
        }


@dataclass
class ChildChunk:
    chunk_id: str
    parent_id: str
    document_id: str
    chunk_type: str
    template: str
    text: str
    page_span: tuple[int, int]
    source_block_ids: list[str]
    bbox_refs: list[BBoxRef] = field(default_factory=list)
    heading_path: list[str] = field(default_factory=list)
    context_before: str | None = None
    context_after: str | None = None
    token_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _drop_none(
            {
                "chunk_id": self.chunk_id,
                "parent_id": self.parent_id,
                "document_id": self.document_id,
                "chunk_type": self.chunk_type,
                "template": self.template,
                "text": self.text,
                "page_span": list(self.page_span),
                "source_block_ids": list(self.source_block_ids),
                "bbox_refs": [ref.to_dict() for ref in self.bbox_refs],
                "heading_path": list(self.heading_path),
                "context_before": self.context_before,
                "context_after": self.context_after,
                "token_count": self.token_count,
                "metadata": dict(self.metadata),
            }
        )


@dataclass
class ChunkPackage:
    document_id: str
    document_type: str
    parser_used: str
    chunker_used: str
    parent_chunks: list[ParentChunk] = field(default_factory=list)
    child_chunks: list[ChildChunk] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    parse_report: ParseReport = field(default_factory=ParseReport)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_blocks: bool = True, include_debug: bool = False) -> dict[str, Any]:
        out = {
            "document_id": self.document_id,
            "document_type": self.document_type,
            "parser_used": self.parser_used,
            "chunker_used": self.chunker_used,
            "parent_chunk_count": len(self.parent_chunks),
            "child_chunk_count": len(self.child_chunks),
            "parent_chunks": [p.to_dict() for p in self.parent_chunks],
            "child_chunks": [c.to_dict() for c in self.child_chunks],
            "parse_report": self.parse_report.to_dict(),
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }
        if include_blocks:
            out["blocks"] = [b.to_dict() for b in self.blocks]
        if include_debug:
            out["debug"] = dict(self.debug)
        return out
