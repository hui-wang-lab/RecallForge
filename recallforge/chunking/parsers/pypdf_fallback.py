"""pypdf fallback adapter that emits basic paragraph blocks."""
from __future__ import annotations

import re
from pathlib import Path

from recallforge.chunking.core.ids import block_id
from recallforge.chunking.ir.models import Block, ParsedDocument
from recallforge.chunking.parsers.base import ParserAdapter, ParserConfig
from recallforge.chunking.parsers.utils import file_document_id, parsed_document_from_blocks
from recallforge.chunking.pdf_parser import clean_chunk_text, clean_page_texts, extract_metadata_from_page_text

_PARAGRAPH_SEP_RE = re.compile(r"\n\s*\n")
# Punctuation that unambiguously ends a sentence or clause in the text flow
_LINE_TERMINAL_RE = re.compile(r'[。！？…；：.!?」』""»›]$')
# Spaces between two CJK characters are pypdf text-run artifacts, not semantic
_CJK_SPACE_RE = re.compile(
    "(?<=[一-鿿㐀-䶿　-〿＀-￯])"
    r"\s+"
    "(?=[一-鿿㐀-䶿　-〿＀-￯])"
)
# Patterns that definitively start a new logical block (chapter / article / section)
_NEW_BLOCK_START_RE = re.compile(
    r'^(?:'
    r'第[零一二三四五六七八九十百千万\d]+[章节条款篇]'
    r'|chapter\s+\d+'
    r'|section\s+\d+'
    r'|article\s+\d+'
    r'|\d+(?:\.\d+){1,3}\s+\S'   # "1.2 heading" — requires at least one sub-level
    r')',
    re.IGNORECASE,
)


class PyPdfFallbackParser(ParserAdapter):
    name = "pypdf"

    def is_available(self) -> bool:
        try:
            import pypdf  # noqa: F401
        except ImportError:
            return False
        return True

    def parse(self, path: str | Path, config: ParserConfig) -> ParsedDocument:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF parsing requires optional dependency pypdf.") from exc

        path_obj = Path(path)
        document_id = file_document_id(path_obj)
        reader = PdfReader(str(path_obj))

        raw_page_texts: list[tuple[int, str]] = []
        for page_index, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            raw_page_texts.append((page_index + 1, text.strip()))

        page_texts = clean_page_texts(raw_page_texts)
        blocks: list[Block] = []
        reading_order = 0

        for page_number, page_text in page_texts:
            metadata = extract_metadata_from_page_text(page_number, page_text)
            heading_path = _heading_path(metadata)
            for paragraph in _paragraphs(page_text):
                cleaned = clean_chunk_text(paragraph)
                if not cleaned:
                    continue
                block_type = "paragraph"
                if _looks_like_heading(cleaned):
                    block_type = "heading"
                    heading_path = [cleaned.splitlines()[0].strip()]
                blocks.append(
                    Block(
                        block_id=block_id(document_id, page_number, reading_order, block_type),
                        document_id=document_id,
                        page_number=page_number,
                        block_type=block_type,
                        text=cleaned,
                        reading_order=reading_order,
                        heading_path=list(heading_path),
                        metadata={"source_parser": self.name},
                    )
                )
                reading_order += 1

        return parsed_document_from_blocks(
            path=path_obj,
            parser_used=self.name,
            parser_fallback_chain=[self.name],
            blocks=blocks,
        )


def _paragraphs(text: str) -> list[str]:
    parts = _PARAGRAPH_SEP_RE.split(text)
    if len(parts) > 1:
        # Double-newline gives paragraph boundaries, but each segment may still
        # have PDF line-level wrapping — apply the same merge logic within each.
        result: list[str] = []
        for part in parts:
            if part.strip():
                result.extend(_merge_wrapped_lines(part.strip().splitlines()))
        return result
    # pypdf often emits one line per PDF text-run with no blank separators.
    # Re-join continuation lines into logical paragraphs.
    return _merge_wrapped_lines(text.splitlines())


def _merge_wrapped_lines(lines: list[str]) -> list[str]:
    """Merge PDF line-level splits back into logical paragraphs.

    A new paragraph starts when the previous line ends with terminal punctuation
    OR the next line begins a recognised structural element (chapter/article/…).
    CJK lines are concatenated without a space; Latin-to-Latin line joins use a space.
    """
    paragraphs: list[str] = []
    current = ""

    for raw in lines:
        line = raw.strip()
        if not line:
            if current:
                paragraphs.append(current)
                current = ""
            continue

        if not current:
            current = line
            continue

        prev_terminal = bool(_LINE_TERMINAL_RE.search(current))
        next_new_block = bool(_NEW_BLOCK_START_RE.match(line))

        if prev_terminal or next_new_block:
            paragraphs.append(current)
            current = line
        else:
            # Determine join character: space only between ASCII words
            sep = (
                " "
                if current[-1].isalpha() and current[-1].isascii()
                and line[0].isalpha() and line[0].isascii()
                else ""
            )
            current = current + sep + line

    if current:
        paragraphs.append(current)

    return [_CJK_SPACE_RE.sub("", p) for p in paragraphs]


def _heading_path(metadata: object) -> list[str]:
    if metadata is None:
        return []
    path: list[str] = []
    chapter = getattr(metadata, "chapter", None)
    section = getattr(metadata, "section", None)
    for value in (chapter, section):
        if value and str(value).strip() not in path:
            path.append(str(value).strip())
    return path


def _looks_like_heading(text: str) -> bool:
    first = text.splitlines()[0].strip()
    if not first or len(first) > 80:
        return False
    # Complete sentences are content, not structural headings
    if _LINE_TERMINAL_RE.search(first):
        return False
    return bool(
        re.match(r"^(chapter|section)\s+\d+", first, re.IGNORECASE)
        or re.match(r"^\d+(?:\.\d+){0,3}\s+\S+", first)
        or re.match(r"^第.+[章节条]\s*", first)
    )
