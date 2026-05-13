"""Book chunker."""
from __future__ import annotations

import re
from collections import OrderedDict

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

_CHAPTER_RE = re.compile(r"^\s*(?:chapter\s+\d+|book\s+\d+|part\s+\d+|第.+[章节卷篇])", re.IGNORECASE)
_TOC_RE = re.compile(r"^\s*(?:contents|table of contents|目录)\s*$", re.IGNORECASE)
_INDEX_RE = re.compile(r"^\s*(?:index|索引)\s*$", re.IGNORECASE)


class BookChunker(TemplateChunker):
    name = "book"

    def chunk(self, document: ParsedDocument, config: ChunkerConfig) -> ChunkingResult:
        blocks = [block for block in ordered_content_blocks(document) if not _is_toc_or_index(block)]
        groups = _chapter_groups(blocks)
        parents = []
        children = []

        for group in groups.values():
            parent = make_parent_from_blocks(document, group)
            local = _book_children(document, parent, group.blocks, config, self.name)
            parent.child_chunk_ids = [child.chunk_id for child in local]
            parents.append(parent)
            children.extend(local)

        return ChunkingResult(chunker_used=self.name, parent_chunks=parents, child_chunks=children)


def _chapter_groups(blocks: list[Block]) -> "OrderedDict[str, BlockGroup]":
    groups: "OrderedDict[str, BlockGroup]" = OrderedDict()
    current = BlockGroup(
        key="Front Matter",
        title="Front Matter",
        heading_path=["Front Matter"],
        metadata={"template_rule": "book_front_matter"},
    )

    for block in blocks:
        first = _first_line(block)
        if _CHAPTER_RE.match(first) or (block.block_type == "heading" and _looks_like_chapter_heading(first)):
            current = BlockGroup(
                key=first,
                title=first,
                heading_path=[first],
                metadata={"template_rule": "book_chapter"},
            )
        elif current.key == "Front Matter" and block.heading_path:
            current.title = block.heading_path[0]
            current.heading_path = [block.heading_path[0]]
            current.key = block.heading_path[0]

        groups.setdefault(current.key, current).blocks.append(block)

    return groups


def _book_children(
    document: ParsedDocument,
    parent,
    blocks: list[Block],
    config: ChunkerConfig,
    template: str,
):
    children = []
    current: list[Block] = []

    def flush() -> None:
        nonlocal current
        if current:
            children.append(
                make_child_from_blocks(
                    document,
                    template,
                    parent,
                    len(children),
                    current,
                    chunk_type="book_section",
                    heading_path=parent.heading_path,
                    metadata={"template_rule": "book_section"},
                )
            )
        current = []

    for block in blocks:
        if block.block_type in MEDIA_TYPES:
            flush()
            children.append(
                make_child_from_blocks(
                    document,
                    template,
                    parent,
                    len(children),
                    [block],
                    heading_path=parent.heading_path,
                    metadata={"template_rule": "book_media_independent"},
                )
            )
            continue
        if block.block_type == "heading" and current:
            flush()
        if current and token_count([*current, block]) > config.child_max_tokens:
            flush()
        current.append(block)

    flush()
    return children


def _first_line(block: Block) -> str:
    return next((line.strip() for line in block.text.splitlines() if line.strip()), "")


def _looks_like_chapter_heading(text: str) -> bool:
    lower = text.lower()
    return lower.startswith(("preface", "appendix", "chapter", "part "))


def _is_toc_or_index(block: Block) -> bool:
    first = _first_line(block)
    return bool(_TOC_RE.match(first) or _INDEX_RE.match(first))

