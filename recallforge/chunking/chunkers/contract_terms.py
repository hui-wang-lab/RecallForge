"""Contract and insurance terms chunker."""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import replace

from recallforge.chunking.chunkers.base import ChunkerConfig, ChunkingResult, TemplateChunker
from recallforge.chunking.chunkers.template_utils import (
    MEDIA_TYPES,
    BlockGroup,
    make_child_from_blocks,
    make_parent_from_blocks,
    ordered_content_blocks,
    token_count,
)
from recallforge.chunking.ir.models import Block, ParsedDocument

_CN_NUM_CHARS = r"一二三四五六七八九十百千万零〇两0-9"
_CN_NUM = rf"(?:[{_CN_NUM_CHARS}]\s*)+"
_CHAPTER_RE = re.compile(
    rf"^\s*(?:第\s*{_CN_NUM}\s*[章编部篇卷]|chapter\s+\d+|part\s+\d+)",
    re.IGNORECASE,
)
_ARTICLE_RE = re.compile(
    rf"^\s*(?:第\s*{_CN_NUM}\s*条|article\s+\d+)",
    re.IGNORECASE,
)
_ARTICLE_START_RE = re.compile(rf"^\s*(第\s*{_CN_NUM}\s*条)\s*(.*)$")
_NUMERIC_ARTICLE_RE = re.compile(r"^\s*\d+(?:\.\d+){0,2}\s+\S+")
_CLAUSE_RE = re.compile(
    rf"^\s*(?:[（(][{_CN_NUM_CHARS}a-zA-Z]+[）)]|[①②③④⑤⑥⑦⑧⑨⑩]|\d+[.)、])"
)
_SENTENCE_END_RE = re.compile(r"[。！？；.!?]$")
_ORPHAN_START_RE = re.compile(r"^[，、；：…）)]")
_MAX_HEADING_LEN = 30

_PAGE_JUNK_PATTERNS = [
    re.compile(r"^第\s*\d+\s*页\s*$"),
    re.compile(r"^page\s+\d+\s*$", re.IGNORECASE),
    re.compile(r"^\d{4,6}\s*$"),
    re.compile(r".*\[\d{4}\].*号\s*$"),
    re.compile(r".*基本条款第[一二三四五六七八九十百千万\d]+版\s*$"),
]
_WIDE_SPACE = re.compile(r"\s{2,}")


class ContractTermsChunker(TemplateChunker):
    """Contract/insurance terms chunker.

    Parent chunks are chapter/section groups. Child chunks are article/clause
    units split at token limits, with tables/figures emitted independently.
    """

    name = "contract_terms"

    def chunk(self, document: ParsedDocument, config: ChunkerConfig) -> ChunkingResult:
        blocks = ordered_content_blocks(document)
        blocks = _repair_split_article_markers(blocks)
        parent_groups, warnings = _contract_parent_groups(blocks)
        parent_chunks = []
        child_chunks = []

        for group in parent_groups.values():
            parent = make_parent_from_blocks(document, group)
            local_children = _contract_children(document, parent, group.blocks, config, self.name)
            parent.child_chunk_ids = [child.chunk_id for child in local_children]
            parent_chunks.append(parent)
            child_chunks.extend(local_children)

        return ChunkingResult(
            chunker_used=self.name,
            parent_chunks=parent_chunks,
            child_chunks=child_chunks,
            warnings=warnings,
        )


def _contract_parent_groups(blocks: list[Block]) -> tuple["OrderedDict[str, BlockGroup]", list[str]]:
    """Group blocks by chapter/section while keeping articles as child units."""
    groups = _chapter_parent_groups(blocks)
    article_count = _article_heading_count(blocks)
    # MinerU often emits insurance terms as level-2 headings such as
    # "第 1 条 ..." without a higher chapter node. If chapter grouping collapses
    # to one broad parent, prefer article-level parents for better small-to-big
    # context expansion.
    if len(groups) <= 1 and article_count >= 3:
        groups = _article_parent_groups(blocks)
        article_parent_count = sum(
            1
            for group in groups.values()
            if group.metadata.get("template_rule") == "contract_parent_article"
        )
        return groups, [
            f"[contract_parent_fallback] grouped chapterless contract into "
            f"{article_parent_count} article parent chunks"
        ]
    return groups, []


def _chapter_parent_groups(blocks: list[Block]) -> "OrderedDict[str, BlockGroup]":
    groups: "OrderedDict[str, BlockGroup]" = OrderedDict()
    current_key = "Document"
    current_title = "Document"
    current_path = ["Document"]

    for block in blocks:
        first_line = _first_line(block)
        if _is_chapter_heading(block, first_line):
            current_title = first_line
            current_key = first_line
            current_path = [first_line]
        elif (
            block.block_type in {"heading", "title"}
            and block.heading_path
            and block.heading_path[0] != current_title
            and not _is_article_heading(block, first_line)
            and _is_real_section_title(first_line)
        ):
            current_title = block.heading_path[0]
            current_key = current_title
            current_path = [current_title]

        group = groups.setdefault(
            current_key,
            BlockGroup(
                key=current_key,
                title=current_title,
                heading_path=list(current_path),
                metadata={"template_rule": "contract_parent_chapter"},
            ),
        )
        group.blocks.append(block)

    return groups


def _article_parent_groups(blocks: list[Block]) -> "OrderedDict[str, BlockGroup]":
    """Fallback for contract PDFs that have article headings but no chapter headings."""
    groups: "OrderedDict[str, BlockGroup]" = OrderedDict()
    current_group: BlockGroup | None = None
    article_index = 0

    for block in blocks:
        first_line = _first_line(block)
        if _is_article_heading(block, first_line):
            article_index += 1
            title = _article_heading_title(first_line)
            key = f"article:{article_index}:{title}"
            current_group = BlockGroup(
                key=key,
                title=title,
                heading_path=[title],
                metadata={
                    "template_rule": "contract_parent_article",
                    "parent_fallback": "article",
                    "article_index": article_index,
                },
            )
            groups[key] = current_group
        elif current_group is None:
            current_group = groups.setdefault(
                "Document",
                BlockGroup(
                    key="Document",
                    title="Document",
                    heading_path=["Document"],
                    metadata={"template_rule": "contract_parent_preamble"},
                ),
            )

        current_group.blocks.append(block)

    return groups


def _repair_split_article_markers(blocks: list[Block]) -> list[Block]:
    """Merge Docling-split Chinese article markers back into their body text.

    Some PDFs emit the article label in a narrow left-side text box while the
    article body is emitted as a wider overlapping block. In the sample file,
    Docling returns ``第一条 定本办法。`` before the title and leaves the body
    ending with ``制``. This pass repairs that to
    ``第一条 ... 制定本办法。`` before chunking.
    """
    repaired: list[Block | None] = list(blocks)
    consumed: set[int] = set()

    for marker_index, marker in enumerate(blocks):
        if marker_index in consumed:
            continue
        match = _ARTICLE_START_RE.match(_first_line(marker))
        if not match or marker.bbox is None:
            continue

        label, tail = match.group(1), match.group(2).strip()
        if not tail:
            continue

        body_index = _find_split_article_body(blocks, marker_index)
        if body_index is None or body_index in consumed:
            continue

        body = blocks[body_index]
        combined_text = _combine_article_text(label, body.text, tail)
        repaired[body_index] = replace(
            body,
            text=combined_text,
            markdown=combined_text if body.markdown else body.markdown,
            block_type="paragraph" if body.block_type in {"list_item", "text"} else body.block_type,
            metadata={
                **body.metadata,
                "article_marker_repaired": True,
                "article_marker_block_id": marker.block_id,
                "merged_source_block_ids": [body.block_id, marker.block_id],
            },
        )
        repaired[marker_index] = None
        consumed.add(marker_index)

    return [block for block in repaired if block is not None]


def _find_split_article_body(blocks: list[Block], marker_index: int) -> int | None:
    marker = blocks[marker_index]
    if marker.bbox is None:
        return None

    best_index: int | None = None
    best_score = -1.0
    for index, candidate in enumerate(blocks):
        if index == marker_index or candidate.page_number != marker.page_number or candidate.bbox is None:
            continue
        if candidate.block_type in MEDIA_TYPES or candidate.block_type in {"heading", "title"}:
            continue
        first = _first_line(candidate)
        if not first or _ARTICLE_START_RE.match(first):
            continue

        overlap = _vertical_overlap_ratio(marker, candidate)
        if overlap <= 0:
            continue
        marker_width = marker.bbox.x1 - marker.bbox.x0
        candidate_width = candidate.bbox.x1 - candidate.bbox.x0
        is_wide_body = candidate_width > marker_width * 1.8
        is_previous_unfinished = (
            candidate.reading_order < marker.reading_order
            and not _SENTENCE_END_RE.search(first)
        )
        if not is_wide_body and not is_previous_unfinished:
            continue

        distance = abs(candidate.reading_order - marker.reading_order)
        score = overlap * 100 - distance
        if candidate.heading_path and marker.heading_path and candidate.heading_path != marker.heading_path:
            score += 20
        if score > best_score:
            best_score = score
            best_index = index

    return best_index


def _vertical_overlap_ratio(left: Block, right: Block) -> float:
    if left.bbox is None or right.bbox is None:
        return 0.0
    overlap = min(left.bbox.y1, right.bbox.y1) - max(left.bbox.y0, right.bbox.y0)
    if overlap <= 0:
        return 0.0
    height = max(1.0, min(left.bbox.y1 - left.bbox.y0, right.bbox.y1 - right.bbox.y0))
    return overlap / height


def _combine_article_text(label: str, body: str, tail: str) -> str:
    body = body.strip()
    tail = tail.strip()
    if not tail:
        return f"{label} {body}".strip()
    separator = "" if body[-1:] and tail[:1] and not body[-1].isspace() else " "
    return f"{label} {body}{separator}{tail}".strip()


def _contract_children(
    document: ParsedDocument,
    parent,
    blocks: list[Block],
    config: ChunkerConfig,
    template: str,
):
    children = []
    current: list[Block] = []
    active_article: str | None = None

    def flush() -> None:
        nonlocal current
        if current:
            if _has_substantive_body(current):
                children.append(
                    make_child_from_blocks(
                        document,
                        template,
                        parent,
                        len(children),
                        current,
                        chunk_type="contract_clause",
                        heading_path=_child_heading(parent.heading_path, active_article),
                        metadata={
                            "article": active_article,
                            "template_rule": "contract_article_or_clause",
                        },
                    )
                )
        current = []

    for block in blocks:
        if _is_page_junk(block.text):
            continue

        first_line = _first_line(block)
        if block.block_type in MEDIA_TYPES:
            flush()
            children.append(
                make_child_from_blocks(
                    document,
                    template,
                    parent,
                    len(children),
                    [block],
                    heading_path=_child_heading(parent.heading_path, active_article),
                    metadata={
                        "article": active_article,
                        "template_rule": "contract_media_independent",
                    },
                )
            )
            continue

        if _is_chapter_heading(block, first_line) and not current:
            current.append(block)
            continue

        is_article = _is_article_heading(block, first_line)
        if is_article and current:
            flush()
        if is_article:
            active_article = _article_heading_title(first_line)

        if current and token_count([*current, block]) > config.child_max_tokens and not _is_clause_continuation(block):
            flush()

        current.append(block)

    flush()
    _annotate_article_parts(children)
    return children


def _is_page_junk(text: str) -> bool:
    """Return True for common PDF page-footer / page-header artifacts."""
    stripped = text.strip()
    if not stripped or len(stripped) > 120:
        return False
    if any(pattern.search(stripped) for pattern in _PAGE_JUNK_PATTERNS):
        return True
    if len(stripped) > 20:
        segments = [segment.strip() for segment in _WIDE_SPACE.split(stripped) if segment.strip()]
        if len(segments) > 1 and all(
            any(pattern.search(segment) for pattern in _PAGE_JUNK_PATTERNS)
            for segment in segments
        ):
            return True
    return False


def _is_real_section_title(first_line: str) -> bool:
    """Return False for text that looks like a sentence rather than a section title."""
    if not first_line:
        return False
    if _CLAUSE_RE.match(first_line):
        return False
    if _SENTENCE_END_RE.search(first_line):
        return False
    if _ORPHAN_START_RE.match(first_line):
        return False
    if len(first_line) > _MAX_HEADING_LEN:
        return False
    return True


def _is_chapter_heading(block: Block, first_line: str) -> bool:
    if not _is_real_section_title(first_line):
        return False
    if _CHAPTER_RE.match(first_line):
        return True
    return block.block_type == "heading" and not _ARTICLE_RE.match(first_line)


def _is_article_heading(block: Block, first_line: str) -> bool:
    return bool(_ARTICLE_RE.match(first_line)) or (
        block.block_type == "heading"
        and (bool(_ARTICLE_RE.search(first_line)) or bool(_NUMERIC_ARTICLE_RE.match(first_line)))
    )


def _article_heading_count(blocks: list[Block]) -> int:
    return sum(1 for block in blocks if _is_article_heading(block, _first_line(block)))


def _explicit_chapter_heading_count(blocks: list[Block]) -> int:
    return sum(1 for block in blocks if _CHAPTER_RE.match(_first_line(block)))


def _article_heading_title(first_line: str) -> str:
    stripped = first_line.strip()
    match = _ARTICLE_START_RE.match(stripped)
    if not match:
        return stripped

    label = _canonical_article_label(match.group(1))
    tail = match.group(2).strip()
    if not tail:
        return label

    first_token = tail.split()[0].strip("。；;:：，,")
    if first_token and len(first_token) <= _MAX_HEADING_LEN:
        return f"{label} {first_token}"

    sentence_head = re.split(r"[。；;:：，,]", tail, maxsplit=1)[0].strip()
    if sentence_head and len(sentence_head) <= _MAX_HEADING_LEN:
        return f"{label} {sentence_head}"

    return f"{label} {tail[:_MAX_HEADING_LEN].strip()}".strip()


def _canonical_article_label(label: str) -> str:
    return re.sub(r"\s+", "", label)


def _is_clause_continuation(block: Block) -> bool:
    return block.block_type == "list_item" or bool(_CLAUSE_RE.match(_first_line(block)))


def _has_substantive_body(blocks: list[Block]) -> bool:
    """Return whether a candidate article chunk has content beyond headings."""
    for block in blocks:
        first_line = _first_line(block)
        if block.block_type not in {"heading", "title"}:
            return True
        if not (_is_chapter_heading(block, first_line) or _is_article_heading(block, first_line)):
            return True
        body_lines = [line.strip() for line in block.text.splitlines()[1:] if line.strip()]
        if body_lines:
            return True
    return False


def _annotate_article_parts(children) -> None:
    by_article: dict[str, list] = {}
    for child in children:
        article = child.metadata.get("article")
        if article:
            by_article.setdefault(article, []).append(child)

    for article_children in by_article.values():
        if len(article_children) <= 1:
            continue
        total = len(article_children)
        for index, child in enumerate(article_children, start=1):
            child.metadata["article_part_index"] = index
            child.metadata["article_part_count"] = total


def _first_line(block: Block) -> str:
    return next((line.strip() for line in block.text.splitlines() if line.strip()), "")


def _child_heading(parent_heading: list[str], article: str | None) -> list[str]:
    if article and article not in parent_heading:
        return [*parent_heading, article]
    return list(parent_heading)
