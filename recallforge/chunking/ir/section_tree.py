"""Build a lightweight section tree from parsed blocks."""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass

from recallforge.chunking.core.ids import section_id
from recallforge.chunking.ir.models import Block, ParsedDocument, SectionNode

ROOT_TITLE = "Document"

# Matches Chinese legal article headings: 第X条/款/章/节 (X = Chinese numerals or digits)
_CN_NUM_WITH_SPACES = r"(?:[零一二三四五六七八九十百千万\d]\s*)+"
_CN_LEGAL_ARTICLE_RE = re.compile(
    rf'^第\s*{_CN_NUM_WITH_SPACES}[条款章节]{{1,2}}(?:\s|$)'
)
_STRUCTURED_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"\d+(?:\.\d+){0,4}\s+\S+|"
    r"chapter\s+\d+\b.*|section\s+\d+\b.*|part\s+\d+\b.*|appendix\b.*|"
    rf"第\s*{_CN_NUM_WITH_SPACES}[章节目篇卷]\s*\S*"
    r")",
    re.IGNORECASE,
)
_MAX_ARTICLE_HEADING_LEN = 40
# Parser (e.g. docling) sometimes marks long sentences as headings; cap real section titles.
_MAX_SECTION_HEADING_LEN = 30
_MAX_INFERRED_HEADING_LEN = 80
_SENTENCE_END_RE = re.compile(r'[。！？…；.!?]$')
_ORPHAN_START_RE = re.compile(r'^[，、；：…—]')
_NUMBERED_HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,5})\s+\S+")
_CN_HEADING_RE = re.compile(rf"^\s*第\s*{_CN_NUM_WITH_SPACES}([章节目篇卷条款])")
# Matches date strings like "2025 年 8 月 6 日" or "2025/08/06" to prevent false heading promotion
_DATE_RE = re.compile(r'^\d{4}\s*[年/\-\.]\s*\d{1,2}')


@dataclass(frozen=True)
class HeadingCandidate:
    title: str
    level: int
    reason: str


def build_section_tree(document: ParsedDocument) -> ParsedDocument:
    """Populate section IDs and a simple heading-path section tree.

    Phase 1 keeps this intentionally conservative: it trusts parser-provided
    heading paths when present and otherwise carries forward the most recent
    heading block on the reading path.

    When a parser emits all blocks as generic paragraphs (e.g. pypdf fallback),
    this pass promotes short blocks matching Chinese legal article patterns
    (第X条/款/章/节) to heading blocks so the section tree has meaningful nodes.
    """
    sections: "OrderedDict[tuple[str, ...], SectionNode]" = OrderedDict()
    current_path: list[str] = []
    font_levels = _font_size_levels(document.blocks)

    def ensure(path: list[str], page_number: int) -> SectionNode:
        clean_path = _clean_path(path) or [ROOT_TITLE]
        for level in range(1, len(clean_path) + 1):
            partial = tuple(clean_path[:level])
            if partial in sections:
                node = sections[partial]
                node.page_start = min(node.page_start, page_number)
                node.page_end = max(node.page_end, page_number)
                continue
            parent = tuple(clean_path[: level - 1])
            parent_section_id = sections[parent].section_id if parent in sections else None
            sections[partial] = SectionNode(
                section_id=section_id(document.document_id, list(partial)),
                parent_section_id=parent_section_id,
                title=clean_path[level - 1],
                level=level,
                page_start=page_number,
                page_end=page_number,
                heading_path=list(partial),
            )
        return sections[tuple(clean_path)]

    for block in document.blocks:
        if block.heading_path:
            current_path = _clean_path(block.heading_path)
            block.metadata.setdefault("heading_level", len(current_path))
            block.metadata.setdefault("heading_reason", "parser_heading_path")
        elif block.block_type in {"title", "heading"} and block.text.strip():
            first_line = next((line.strip() for line in block.text.splitlines() if line.strip()), "")
            candidate = _classify_heading(block, first_line, font_levels, parser_heading=True)
            if candidate is not None:
                current_path = _path_with_level(current_path, candidate)
                block.metadata["heading_level"] = candidate.level
                block.metadata["heading_reason"] = candidate.reason
        else:
            inferred = _infer_heading(block, font_levels)
            if inferred is not None:
                current_path = _path_with_level(current_path, inferred)
                block.block_type = "heading"  # promote so downstream chunkers see it
                block.metadata["heading_level"] = inferred.level
                block.metadata["heading_reason"] = inferred.reason

        node = ensure(current_path, block.page_number)
        block.section_id = node.section_id
        block.heading_path = list(node.heading_path)
        node.block_ids.append(block.block_id)

    document.section_tree = list(sections.values())
    return document


def _infer_heading(block: Block, font_levels: dict[float, int]) -> HeadingCandidate | None:
    """Return a heading candidate if a paragraph looks like a structural heading."""
    if block.block_type not in {"paragraph", "text"}:
        return None
    text = block.text.strip()
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first_line or len(first_line) > _MAX_INFERRED_HEADING_LEN:
        return None
    if _ORPHAN_START_RE.match(first_line):
        return None
    # Bug 2 fix: dates like "2025 年 8 月 6 日" must not become headings
    if _DATE_RE.match(first_line):
        return None
    # Bug 3 fix: legal articles ending with a sentence (。！？…；) are content, not headings
    if (len(first_line) <= _MAX_ARTICLE_HEADING_LEN
            and _CN_LEGAL_ARTICLE_RE.match(first_line)
            and not _SENTENCE_END_RE.search(first_line)):
        return HeadingCandidate(
            first_line,
            _heading_level(block, first_line, font_levels),
            "legal_article_pattern",
        )
    if not _SENTENCE_END_RE.search(first_line) and _STRUCTURED_HEADING_RE.match(first_line):
        return HeadingCandidate(
            first_line,
            _heading_level(block, first_line, font_levels),
            "structured_heading_pattern",
        )
    return None


def _classify_heading(
    block: Block,
    first_line: str,
    font_levels: dict[float, int],
    *,
    parser_heading: bool = False,
) -> HeadingCandidate | None:
    if not first_line:
        return None
    if _ORPHAN_START_RE.match(first_line):
        return None
    if _DATE_RE.match(first_line):
        return None
    if _SENTENCE_END_RE.search(first_line) and not _STRUCTURED_HEADING_RE.match(first_line):
        return None
    if len(first_line) > _MAX_INFERRED_HEADING_LEN:
        return None
    if len(first_line) > _MAX_SECTION_HEADING_LEN and not _STRUCTURED_HEADING_RE.match(first_line):
        return None
    reason = "parser_heading" if parser_heading else "heading_classifier"
    return HeadingCandidate(first_line, _heading_level(block, first_line, font_levels), reason)


def _heading_level(block: Block, title: str, font_levels: dict[float, int]) -> int:
    for key in ("heading_level", "text_level", "level"):
        value = block.metadata.get(key)
        if isinstance(value, int):
            return max(1, min(value, 6))

    match = _NUMBERED_HEADING_RE.match(title)
    if match:
        return max(1, min(match.group(1).count(".") + 1, 6))

    cn_match = _CN_HEADING_RE.match(title)
    if cn_match:
        marker = cn_match.group(1)
        if marker in {"章", "篇", "卷"}:
            return 1
        if marker in {"节", "目", "条"}:
            return 2
        return 3

    lower = title.lower()
    if lower.startswith(("chapter ", "part ", "appendix")):
        return 1
    if lower.startswith("section "):
        return 2

    font_size = _font_size(block)
    if font_size is not None and font_size in font_levels:
        return font_levels[font_size]
    return 1


def _path_with_level(current_path: list[str], candidate: HeadingCandidate) -> list[str]:
    title = candidate.title.strip()
    if not title:
        return current_path
    if candidate.level <= 1 or not current_path:
        return [title]
    parent_path = current_path[: candidate.level - 1]
    if title in parent_path:
        return parent_path[: parent_path.index(title) + 1]
    return [*parent_path, title]


def _font_size_levels(blocks: list[Block]) -> dict[float, int]:
    sizes = sorted(
        {
            value
            for block in blocks
            for value in [_font_size(block)]
            if value is not None
        },
        reverse=True,
    )
    return {size: min(index + 1, 6) for index, size in enumerate(sizes[:6])}


def _font_size(block: Block) -> float | None:
    for key in ("font_size", "size"):
        value = block.metadata.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _clean_path(path: list[str]) -> list[str]:
    out: list[str] = []
    for item in path:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def top_level_section_for_block(block: Block, document: ParsedDocument) -> SectionNode | None:
    if not block.heading_path:
        return document.section_tree[0] if document.section_tree else None
    wanted = tuple(block.heading_path[:1])
    for section in document.section_tree:
        if tuple(section.heading_path) == wanted:
            return section
    return document.section_tree[0] if document.section_tree else None
