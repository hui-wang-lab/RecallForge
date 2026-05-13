"""Docling parser adapter."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from recallforge.chunking.core.ids import block_id
from recallforge.chunking.docling_parser import is_docling_available, parse_pdf_with_docling
from recallforge.chunking.ir.models import Block, Page, ParsedDocument
from recallforge.chunking.ir.normalize import extract_bbox, page_size_from_value
from recallforge.chunking.parsers.base import ParserAdapter, ParserConfig
from recallforge.chunking.parsers.utils import (
    blocks_from_structured_chunks,
    file_document_id,
    normalized_block_type,
    parsed_document_from_blocks,
)


class DoclingPdfParser(ParserAdapter):
    name = "docling"

    def is_available(self) -> bool:
        return is_docling_available()

    def parse(self, path: str | Path, config: ParserConfig) -> ParsedDocument:
        native_document = _parse_with_native_docling(path)
        if native_document is not None and native_document.blocks:
            return native_document

        parsed_chunks = parse_pdf_with_docling(path, max_tokens=config.max_tokens)
        return blocks_from_structured_chunks(
            path=path,
            parser_used=self.name,
            parsed_chunks=parsed_chunks,
        )


def _parse_with_native_docling(path: str | Path) -> ParsedDocument | None:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        return None

    result = DocumentConverter().convert(str(path))
    doc = getattr(result, "document", None)
    if doc is None:
        return None
    return document_from_docling_document(path=path, doc=doc)


def document_from_docling_document(*, path: str | Path, doc: Any) -> ParsedDocument:
    """Convert a Docling document object into layout-aware IR blocks.

    The adapter intentionally uses duck-typing because Docling has evolved its
    public model shape across versions. Known fields are consumed when present;
    otherwise the parser falls back to conservative text extraction.
    """
    document_id = file_document_id(path)
    page_sizes = _docling_page_sizes(doc)
    blocks: list[Block] = []
    heading_stack: list[str] = []

    for reading_order, item in enumerate(_iter_doc_items(doc)):
        block_type = _block_type(item)
        text, markdown, html = _text_markdown_html(item, block_type)
        caption = _caption(item)
        if caption and caption not in text:
            text = f"{caption}\n\n{text}".strip()
            if markdown:
                markdown = f"{caption}\n\n{markdown}".strip()
        if not text.strip() and block_type == "figure":
            text = caption or "[Figure]"
        if not text.strip():
            continue

        page_number = _page_number(item)
        if block_type == "heading":
            level = _heading_level(item)
            heading_stack = heading_stack[: max(level - 1, 0)]
            heading_stack.append(text.splitlines()[0].strip())
        elif not heading_stack:
            heading_stack = _meta_headings(item)

        blocks.append(
            Block(
                block_id=block_id(document_id, page_number, reading_order, block_type),
                document_id=document_id,
                page_number=page_number,
                block_type=block_type,
                text=text.strip(),
                html=html,
                markdown=markdown,
                bbox=_bbox(item),
                reading_order=reading_order,
                heading_path=list(heading_stack),
                caption=caption,
                metadata={
                    "source_parser": "docling",
                    "docling_label": _label_text(item),
                    "raw_class": item.__class__.__name__,
                },
            )
        )

    document = parsed_document_from_blocks(
        path=path,
        parser_used="docling",
        parser_fallback_chain=["docling"],
        blocks=blocks,
        metadata={
            "adapter_note": "Phase 2 adapter normalized native Docling document items into IR blocks.",
            "layout_source": "docling_document",
        },
    )
    pages = _pages_from_blocks_and_sizes(blocks, page_sizes)
    if pages:
        document.pages = pages
        document.parse_report.page_count = len(pages)
    return document


def _iter_doc_items(doc: Any) -> list[Any]:
    if hasattr(doc, "iterate_items"):
        items: list[Any] = []
        for entry in doc.iterate_items():
            if isinstance(entry, tuple) and entry:
                items.append(entry[0])
            else:
                items.append(entry)
        return items

    items = []
    for attr in ("texts", "tables", "pictures", "figures", "items"):
        value = getattr(doc, attr, None)
        if isinstance(value, dict):
            items.extend(value.values())
        elif isinstance(value, list):
            items.extend(value)
    return items


def _block_type(item: Any) -> str:
    label = _label_text(item)
    if label in {"section_header", "title", "heading"}:
        return "heading"
    if label in {"picture", "image", "figure"}:
        return "figure"
    return normalized_block_type(label)


def _label_text(item: Any) -> str:
    label = getattr(item, "label", None)
    value = getattr(label, "value", label)
    if value is None:
        return item.__class__.__name__.lower()
    return str(value).lower()


def _text_markdown_html(item: Any, block_type: str) -> tuple[str, str | None, str | None]:
    if block_type == "table":
        markdown = _call_string_method(item, ("export_to_markdown", "to_markdown"))
        html = _call_string_method(item, ("export_to_html", "to_html"))
        text = markdown or _item_text(item)
        return text, markdown, html

    markdown = _call_string_method(item, ("export_to_markdown",))
    text = _item_text(item) or markdown or ""
    return text, markdown if markdown and markdown != text else None, None


def _item_text(item: Any) -> str:
    for attr in ("text", "orig", "name"):
        value = getattr(item, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for method in ("export_to_text", "to_text"):
        value = _call_string_method(item, (method,))
        if value:
            return value
    return ""


def _call_string_method(item: Any, names: tuple[str, ...]) -> str | None:
    for name in names:
        method = getattr(item, name, None)
        if not callable(method):
            continue
        try:
            value = method()
        except TypeError:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _caption(item: Any) -> str | None:
    parts: list[str] = []
    for attr in ("caption", "captions"):
        value = getattr(item, attr, None)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif isinstance(value, list):
            for part in value:
                text = _item_text(part) if not isinstance(part, str) else part
                if text.strip():
                    parts.append(text.strip())
    return " ".join(parts) if parts else None


def _page_number(item: Any) -> int:
    for prov in _provenance(item):
        for attr in ("page_no", "page", "page_number"):
            value = getattr(prov, attr, None)
            if isinstance(value, int):
                return value
    for attr in ("page_no", "page", "page_number"):
        value = getattr(item, attr, None)
        if isinstance(value, int):
            return value
    return 1


def _bbox(item: Any):
    for prov in _provenance(item):
        bbox = extract_bbox(prov)
        if bbox is not None:
            return bbox
    return extract_bbox(item)


def _provenance(item: Any) -> list[Any]:
    prov = getattr(item, "prov", None)
    if prov is None:
        return []
    if isinstance(prov, (list, tuple)):
        return list(prov)
    return [prov]


def _heading_level(item: Any) -> int:
    for attr in ("level", "text_level"):
        value = getattr(item, attr, None)
        if isinstance(value, int):
            return max(1, min(value, 6))
    return 1


def _meta_headings(item: Any) -> list[str]:
    meta = getattr(item, "meta", None)
    headings = getattr(meta, "headings", None)
    if isinstance(headings, list):
        return [str(heading).strip() for heading in headings if str(heading).strip()]
    return []


def _docling_page_sizes(doc: Any) -> dict[int, tuple[float | None, float | None]]:
    pages = getattr(doc, "pages", None)
    if not pages:
        return {}
    items = pages.items() if isinstance(pages, dict) else enumerate(pages, start=1)
    out: dict[int, tuple[float | None, float | None]] = {}
    for key, page in items:
        try:
            page_number = int(key)
        except (TypeError, ValueError):
            page_number = getattr(page, "page_no", None) or getattr(page, "page_number", None)
        if not isinstance(page_number, int):
            continue
        out[page_number] = page_size_from_value(page)
    return out


def _pages_from_blocks_and_sizes(
    blocks: list[Block],
    page_sizes: dict[int, tuple[float | None, float | None]],
) -> list[Page]:
    pages: dict[int, Page] = {}
    for page_number, (width, height) in page_sizes.items():
        pages[page_number] = Page(page_number=page_number, width=width, height=height)
    for block in blocks:
        page = pages.setdefault(block.page_number, Page(page_number=block.page_number))
        page.block_ids.append(block.block_id)
    return [pages[key] for key in sorted(pages)]
