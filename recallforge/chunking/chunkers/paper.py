"""Academic paper chunker."""
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

_SECTION_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\s+)?"
    r"(abstract|introduction|background|methods?|methodology|results?|discussion|conclusion|references|appendix)\b",
    re.IGNORECASE,
)


class PaperChunker(TemplateChunker):
    name = "paper"

    def chunk(self, document: ParsedDocument, config: ChunkerConfig) -> ChunkingResult:
        blocks = ordered_content_blocks(document)
        groups = _section_groups(blocks)
        parents = []
        children = []

        for group in groups.values():
            parent = make_parent_from_blocks(document, group)
            local = _paper_children(document, parent, group.blocks, config, self.name, group.title)
            parent.child_chunk_ids = [child.chunk_id for child in local]
            parents.append(parent)
            children.extend(local)

        return ChunkingResult(chunker_used=self.name, parent_chunks=parents, child_chunks=children)


def _section_groups(blocks: list[Block]) -> "OrderedDict[str, BlockGroup]":
    groups: "OrderedDict[str, BlockGroup]" = OrderedDict()
    current = BlockGroup(
        key="Front Matter",
        title="Front Matter",
        heading_path=["Front Matter"],
        metadata={"template_rule": "paper_front_matter"},
    )

    for block in blocks:
        title = _section_title(block)
        if title:
            current = BlockGroup(
                key=title,
                title=title,
                heading_path=[title],
                metadata={"template_rule": "paper_section"},
            )
        elif current.key == "Front Matter" and block.heading_path:
            current.title = block.heading_path[0]
            current.heading_path = [block.heading_path[0]]

        group = groups.setdefault(current.key, current)
        group.blocks.append(block)

    return groups


def _paper_children(
    document: ParsedDocument,
    parent,
    blocks: list[Block],
    config: ChunkerConfig,
    template: str,
    section_title: str,
):
    children = []
    current: list[Block] = []
    lower_title = section_title.lower()

    def flush(chunk_type: str = "paper_text") -> None:
        nonlocal current
        if current:
            children.append(
                make_child_from_blocks(
                    document,
                    template,
                    parent,
                    len(children),
                    current,
                    chunk_type=chunk_type,
                    heading_path=parent.heading_path,
                    metadata={"section": section_title, "template_rule": chunk_type},
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
                    metadata={"section": section_title, "template_rule": "paper_media_independent"},
                )
            )
            continue

        if "abstract" in lower_title:
            current.append(block)
            continue
        if "references" in lower_title:
            current.append(block)
            continue
        if current and token_count([*current, block]) > config.child_max_tokens:
            flush()
        current.append(block)

    if "abstract" in lower_title:
        flush("paper_abstract")
    elif "references" in lower_title:
        flush("paper_references")
    else:
        flush()
    return children


def _section_title(block: Block) -> str | None:
    first = next((line.strip() for line in block.text.splitlines() if line.strip()), "")
    if block.block_type == "heading" and first:
        return first
    if _SECTION_RE.match(first):
        return first
    return None
