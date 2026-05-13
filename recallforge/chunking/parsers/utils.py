"""Shared helpers for parser adapters."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from recallforge.chunking.core.ids import block_id
from recallforge.chunking.ir.models import Block, Page, ParsedDocument, ParseReport
from recallforge.chunking.schema import document_id_from_bytes

_TYPE_MAP = {
    "section_header": "heading",
    "text": "paragraph",
    "paragraph": "paragraph",
    "list": "list_item",
    "list_item": "list_item",
    "table": "table",
    "picture": "figure",
    "figure": "figure",
    "caption": "caption",
    "title": "heading",
    "formula": "formula",
    "header": "header",
    "footer": "footer",
    "page_number": "page_number",
    "interline_equation": "formula",
}


def file_document_id(path: str | Path) -> str:
    with open(path, "rb") as f:
        return document_id_from_bytes(f.read())


def normalized_block_type(value: object) -> str:
    text = str(value or "paragraph").lower()
    return _TYPE_MAP.get(text, text if text in set(_TYPE_MAP.values()) else "paragraph")


def parsed_document_from_blocks(
    *,
    path: str | Path,
    parser_used: str,
    parser_fallback_chain: list[str],
    blocks: list[Block],
    warnings: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ParsedDocument:
    path_obj = Path(path)
    document_id = blocks[0].document_id if blocks else file_document_id(path_obj)
    pages = _pages_from_blocks(blocks)
    report = ParseReport(
        page_count=len(pages),
        block_count=len(blocks),
        table_count=sum(1 for b in blocks if b.block_type == "table"),
        figure_count=sum(1 for b in blocks if b.block_type == "figure"),
        warnings=list(warnings or []),
    )
    return ParsedDocument(
        document_id=document_id,
        source_path=os.path.abspath(str(path_obj)),
        filename=path_obj.name,
        file_type=path_obj.suffix.lower().lstrip(".") or "bin",
        document_type=None,
        parser_used=parser_used,
        parser_fallback_chain=list(parser_fallback_chain),
        pages=pages,
        blocks=blocks,
        parse_report=report,
        metadata=dict(metadata or {}),
    )


def blocks_from_structured_chunks(
    *,
    path: str | Path,
    parser_used: str,
    parsed_chunks: list[dict[str, Any]],
) -> ParsedDocument:
    document_id = file_document_id(path)
    blocks: list[Block] = []

    for index, item in enumerate(parsed_chunks):
        raw_text = str(item.get("raw_text") or "").strip()
        if not raw_text:
            continue
        page_number = _positive_int(item.get("page_number"), default=1)
        headings = [str(h).strip() for h in item.get("headings", []) if str(h).strip()]
        if not headings:
            for key in ("chapter", "section"):
                value = item.get(key)
                if value and str(value).strip() not in headings:
                    headings.append(str(value).strip())
        content_type = normalized_block_type(item.get("content_type"))
        blocks.append(
            Block(
                block_id=block_id(document_id, page_number, index, content_type),
                document_id=document_id,
                page_number=page_number,
                block_type=content_type,
                text=raw_text,
                markdown=raw_text if content_type == "table" else None,
                reading_order=index,
                heading_path=headings,
                metadata={
                    "source_parser": parser_used,
                    "legacy_chapter": item.get("chapter"),
                    "legacy_section": item.get("section"),
                    "domain_hint": item.get("domain_hint"),
                },
            )
        )

    return parsed_document_from_blocks(
        path=path,
        parser_used=parser_used,
        parser_fallback_chain=[parser_used],
        blocks=blocks,
        metadata={"adapter_note": "Phase 1 adapter normalized legacy parser chunks into IR blocks."},
    )


def _pages_from_blocks(blocks: list[Block]) -> list[Page]:
    pages: dict[int, Page] = {}
    for block in blocks:
        page = pages.setdefault(block.page_number, Page(page_number=block.page_number))
        page.block_ids.append(block.block_id)
    return [pages[key] for key in sorted(pages)]


def _positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default

