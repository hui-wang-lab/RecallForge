"""MinerU parser adapter."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from recallforge.chunking.core.ids import block_id
from recallforge.chunking.ir.models import Block, Page, ParsedDocument
from recallforge.chunking.ir.normalize import extract_bbox, page_size_from_value
from recallforge.chunking.mineru_parser import (
    _html_table_to_markdown,
    _markdown_to_chunks,
    _matrix_to_markdown,
    is_mineru_available,
    parse_pdf_with_mineru_artifacts,
)
from recallforge.chunking.parsers.base import ParserAdapter, ParserConfig
from recallforge.chunking.parsers.utils import (
    blocks_from_structured_chunks,
    file_document_id,
    normalized_block_type,
    parsed_document_from_blocks,
)


class MinerUPdfParser(ParserAdapter):
    name = "mineru"

    def is_available(self) -> bool:
        return is_mineru_available()

    def parse(self, path: str | Path, config: ParserConfig) -> ParsedDocument:
        full_md, content_list = parse_pdf_with_mineru_artifacts(path)
        document = document_from_mineru_content_list(
            path=path,
            content_list=content_list,
            full_markdown=full_md,
        )
        if document.blocks:
            return document

        parsed_chunks = _markdown_to_chunks(full_md, max_tokens=config.max_tokens) if full_md.strip() else []
        return blocks_from_structured_chunks(
            path=path,
            parser_used=self.name,
            parsed_chunks=parsed_chunks,
        )


def document_from_mineru_content_list(
    *,
    path: str | Path,
    content_list: list[dict[str, Any]],
    full_markdown: str = "",
) -> ParsedDocument:
    """Convert MinerU ``content_list`` items into layout-aware IR blocks."""
    document_id = file_document_id(path)
    blocks: list[Block] = []
    heading_stack: list[str] = []

    for reading_order, item in enumerate(content_list):
        block_type = _block_type(item)
        page_number = _page_number_from_item(item)
        text, markdown, html = _text_markdown_html(item, block_type)
        caption = _caption_text(item, block_type)

        if caption and caption not in text:
            text = f"{caption}\n\n{text}".strip()
            if markdown:
                markdown = f"{caption}\n\n{markdown}".strip()
        if not text.strip() and block_type == "figure":
            text = caption or "[Figure]"
        if not text.strip():
            continue

        if block_type == "heading":
            level = _heading_level(item)
            heading_stack = heading_stack[: max(level - 1, 0)]
            heading_stack.append(text.splitlines()[0].strip())

        blocks.append(
            Block(
                block_id=block_id(document_id, page_number, reading_order, block_type),
                document_id=document_id,
                page_number=page_number,
                block_type=block_type,
                text=text.strip(),
                html=html,
                markdown=markdown,
                bbox=extract_bbox(item),
                reading_order=reading_order,
                heading_path=list(heading_stack),
                caption=caption,
                confidence=_confidence(item),
                metadata={
                    "source_parser": "mineru",
                    "mineru_type": item.get("type") or item.get("content_type"),
                    "image_path": item.get("img_path") or item.get("image_path"),
                    "raw_keys": sorted(str(key) for key in item.keys()),
                },
            )
        )

    pages = _pages_from_mineru_items(content_list, blocks)
    document = parsed_document_from_blocks(
        path=path,
        parser_used="mineru",
        parser_fallback_chain=["mineru"],
        blocks=blocks,
        metadata={
            "adapter_note": "Phase 2 adapter normalized MinerU content_list into IR blocks.",
            "full_markdown_available": bool(full_markdown.strip()),
            "layout_source": "mineru_content_list",
        },
    )
    if pages:
        document.pages = pages
        document.parse_report.page_count = len(pages)
    return document


def _block_type(item: dict[str, Any]) -> str:
    raw_type = str(item.get("type") or item.get("content_type") or "text").lower()
    if raw_type in {"title", "heading", "section_header"}:
        return "heading"
    if raw_type in {"image", "figure", "picture"}:
        return "figure"
    return normalized_block_type(raw_type)


def _text_markdown_html(item: dict[str, Any], block_type: str) -> tuple[str, str | None, str | None]:
    if block_type == "table":
        html = _table_html(item)
        markdown = _table_markdown(item, html)
        text = markdown or _item_text(item)
        return text, markdown, html

    text = _item_text(item)
    if block_type == "figure":
        text = text or _caption_text(item, block_type)
    return text, None, None


def _table_html(item: dict[str, Any]) -> str | None:
    for key in ("table_body", "html", "table_html"):
        value = item.get(key)
        if isinstance(value, str) and "<table" in value.lower():
            return value
    return None


def _table_markdown(item: dict[str, Any], html: str | None) -> str | None:
    body = (
        item.get("table_body")
        or item.get("html")
        or item.get("table_html")
        or item.get("md")
        or item.get("markdown")
        or item.get("text")
    )
    if isinstance(body, str):
        return _html_table_to_markdown(body) if "<table" in body.lower() else body.strip()
    if isinstance(body, list):
        return _matrix_to_markdown(body)
    if html:
        return _html_table_to_markdown(html)
    return None


def _item_text(item: dict[str, Any]) -> str:
    for key in ("text", "content", "md", "markdown"):
        value = item.get(key)
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return "\n".join(str(part) for part in value if str(part).strip()).strip()
    return ""


def _caption_text(item: dict[str, Any], block_type: str) -> str | None:
    keys = (
        ("table_caption", "caption")
        if block_type == "table"
        else ("img_caption", "image_caption", "figure_caption", "caption")
    )
    parts: list[str] = []
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif isinstance(value, list):
            parts.extend(str(part).strip() for part in value if str(part).strip())
    if not parts:
        return None
    return " ".join(parts)


def _page_number_from_item(item: dict[str, Any]) -> int:
    for key in ("page_idx", "page_index"):
        value = item.get(key)
        if isinstance(value, int):
            return value + 1
    for key in ("page", "page_no", "page_number"):
        value = item.get(key)
        if isinstance(value, int):
            return value
    return 1


def _heading_level(item: dict[str, Any]) -> int:
    value = item.get("text_level") or item.get("level")
    if isinstance(value, int):
        return max(1, min(value, 6))
    return 1


def _confidence(item: dict[str, Any]) -> float | None:
    for key in ("confidence", "score", "prob"):
        value = item.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _pages_from_mineru_items(content_list: list[dict[str, Any]], blocks: list[Block]) -> list[Page]:
    pages: dict[int, Page] = {}
    for item in content_list:
        page_number = _page_number_from_item(item)
        page = pages.setdefault(page_number, Page(page_number=page_number))
        width, height = page_size_from_value(item.get("page_size") or item.get("page_info") or item)
        page.width = page.width or width
        page.height = page.height or height

    for block in blocks:
        page = pages.setdefault(block.page_number, Page(page_number=block.page_number))
        page.block_ids.append(block.block_id)

    return [pages[key] for key in sorted(pages)]
