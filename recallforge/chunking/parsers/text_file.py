"""Plain text and Markdown parser."""
from __future__ import annotations

import re
from pathlib import Path

from recallforge.chunking.core.ids import block_id
from recallforge.chunking.ir.models import Block, Page, ParsedDocument, ParseReport
from recallforge.chunking.parsers.base import ParserAdapter, ParserConfig
from recallforge.chunking.parsers.utils import file_document_id

_PARA_RE = re.compile(r"\n\s*\n")
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+)$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|[①②③④⑤⑥⑦⑧⑨⑩])")


class TextFileParser(ParserAdapter):
    name = "text_file"

    def parse(self, path: str | Path, config: ParserConfig) -> ParsedDocument:
        path_obj = Path(path)
        text = path_obj.read_text(encoding="utf-8-sig", errors="replace")
        document_id = file_document_id(path_obj)
        blocks: list[Block] = []
        heading_path: list[str] = []
        reading_order = 0

        for part in _parts(text):
            block_type = "paragraph"
            stripped = part.strip()
            heading_match = _MD_HEADING_RE.match(stripped)
            if heading_match:
                block_type = "heading"
                heading_path = [heading_match.group(1).strip()]
                stripped = heading_match.group(1).strip()
            elif _looks_like_plain_heading(stripped):
                block_type = "heading"
                heading_path = [stripped.splitlines()[0].strip()]
            elif _LIST_RE.match(stripped):
                block_type = "list_item"

            blocks.append(
                Block(
                    block_id=block_id(document_id, 1, reading_order, block_type),
                    document_id=document_id,
                    page_number=1,
                    block_type=block_type,
                    text=stripped,
                    reading_order=reading_order,
                    heading_path=list(heading_path),
                    metadata={"source_parser": self.name},
                )
            )
            reading_order += 1

        page = Page(page_number=1, block_ids=[block.block_id for block in blocks])
        report = ParseReport(page_count=1, block_count=len(blocks))
        return ParsedDocument(
            document_id=document_id,
            source_path=str(path_obj.resolve()),
            filename=path_obj.name,
            file_type=path_obj.suffix.lower().lstrip(".") or "txt",
            document_type=None,
            parser_used=self.name,
            parser_fallback_chain=[self.name],
            pages=[page],
            blocks=blocks,
            parse_report=report,
            metadata={"layout_source": "text_file"},
        )


def _parts(text: str) -> list[str]:
    parts = [part.strip() for part in _PARA_RE.split(text) if part.strip()]
    if len(parts) > 1:
        return parts
    return [line.strip() for line in text.splitlines() if line.strip()]


def _looks_like_plain_heading(text: str) -> bool:
    first = text.splitlines()[0].strip()
    if len(first) > 80:
        return False
    lower = first.lower()
    return lower in {"abstract", "references", "contents", "faq"} or lower.startswith(
        ("chapter ", "section ", "part ", "appendix ")
    )

