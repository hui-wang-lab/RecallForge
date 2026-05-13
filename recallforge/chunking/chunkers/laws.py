"""Laws and regulations chunker."""
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

_CN_NUM_CHARS = r"\u4e00-\u9fff0-9〇零一二三四五六七八九十百千万"
_CN_NUM = rf"[{_CN_NUM_CHARS}]+"
_LEGAL_PARENT_RE = re.compile(rf"^\s*(?:第{_CN_NUM}[章节编]|chapter\s+\d+|section\s+\d+)", re.IGNORECASE)
_ARTICLE_RE = re.compile(rf"^\s*(?:第{_CN_NUM}条|article\s+\d+)", re.IGNORECASE)
_ITEM_RE = re.compile(rf"^\s*(?:[（(][{_CN_NUM_CHARS}a-zA-Z]+[）)]|\d+[.)、])")


class LawsChunker(TemplateChunker):
    name = "laws"

    def chunk(self, document: ParsedDocument, config: ChunkerConfig) -> ChunkingResult:
        blocks = ordered_content_blocks(document)
        groups = _parent_groups(blocks)
        parents = []
        children = []

        for group in groups.values():
            parent = make_parent_from_blocks(document, group)
            local = _article_children(document, parent, group.blocks, config, self.name)
            parent.child_chunk_ids = [child.chunk_id for child in local]
            parents.append(parent)
            children.extend(local)

        return ChunkingResult(chunker_used=self.name, parent_chunks=parents, child_chunks=children)


def _parent_groups(blocks: list[Block]) -> "OrderedDict[str, BlockGroup]":
    groups: "OrderedDict[str, BlockGroup]" = OrderedDict()
    current = BlockGroup(
        key="Document",
        title="Document",
        heading_path=["Document"],
        metadata={"template_rule": "laws_parent_chapter_section"},
    )

    for block in blocks:
        first = _first_line(block)
        if _LEGAL_PARENT_RE.match(first) and not _ARTICLE_RE.match(first):
            current = BlockGroup(
                key=first,
                title=first,
                heading_path=[first],
                metadata={"template_rule": "laws_parent_chapter_section"},
            )
        elif current.key == "Document" and block.heading_path:
            current = BlockGroup(
                key=block.heading_path[0],
                title=block.heading_path[0],
                heading_path=[block.heading_path[0]],
                metadata={"template_rule": "laws_parent_heading"},
            )

        group = groups.setdefault(current.key, current)
        group.blocks.append(block)

    return groups


def _article_children(
    document: ParsedDocument,
    parent,
    blocks: list[Block],
    config: ChunkerConfig,
    template: str,
):
    children = []
    current: list[Block] = []
    article: str | None = None

    def flush() -> None:
        nonlocal current, article
        if current:
            children.append(
                make_child_from_blocks(
                    document,
                    template,
                    parent,
                    len(children),
                    current,
                    chunk_type="legal_article",
                    heading_path=_heading(parent.heading_path, article),
                    metadata={"article": article, "template_rule": "laws_article"},
                )
            )
        current = []
        article = None

    for block in blocks:
        first = _first_line(block)
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
                    metadata={"template_rule": "laws_media_independent"},
                )
            )
            continue

        starts_article = bool(_ARTICLE_RE.match(first))
        if starts_article and current:
            flush()
        if starts_article:
            article = first
        if current and token_count([*current, block]) > config.child_max_tokens and not _is_item(block):
            flush()
            if starts_article:
                article = first
        current.append(block)

    flush()
    return children


def _first_line(block: Block) -> str:
    return next((line.strip() for line in block.text.splitlines() if line.strip()), "")


def _is_item(block: Block) -> bool:
    return block.block_type == "list_item" or bool(_ITEM_RE.match(_first_line(block)))


def _heading(parent_heading: list[str], article: str | None) -> list[str]:
    if article and article not in parent_heading:
        return [*parent_heading, article]
    return list(parent_heading)
