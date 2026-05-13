"""Technical manual chunker."""
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

_PROCEDURE_RE = re.compile(r"^\s*(?:step\s+\d+|\d+[.)]\s+)", re.IGNORECASE)
_CALLOUT_RE = re.compile(r"^\s*(?:warning|caution|note|danger|important)\b", re.IGNORECASE)
_TROUBLE_RE = re.compile(r"troubleshoot|fault|symptom|cause|remedy|maintenance|installation", re.IGNORECASE)


class ManualChunker(TemplateChunker):
    name = "manual"

    def chunk(self, document: ParsedDocument, config: ChunkerConfig) -> ChunkingResult:
        blocks = ordered_content_blocks(document)
        groups = _manual_groups(blocks)
        parents = []
        children = []

        for group in groups.values():
            parent = make_parent_from_blocks(document, group)
            local = _manual_children(document, parent, group.blocks, config, self.name)
            parent.child_chunk_ids = [child.chunk_id for child in local]
            parents.append(parent)
            children.extend(local)

        return ChunkingResult(chunker_used=self.name, parent_chunks=parents, child_chunks=children)


def _manual_groups(blocks: list[Block]) -> "OrderedDict[str, BlockGroup]":
    groups: "OrderedDict[str, BlockGroup]" = OrderedDict()
    current = BlockGroup(
        key="Manual",
        title="Manual",
        heading_path=["Manual"],
        metadata={"template_rule": "manual_section"},
    )

    for block in blocks:
        first = _first_line(block)
        if block.block_type == "heading" and first:
            current = BlockGroup(
                key=first,
                title=first,
                heading_path=[first],
                metadata={"template_rule": "manual_section"},
            )
        elif current.key == "Manual" and block.heading_path:
            current.title = block.heading_path[0]
            current.heading_path = [block.heading_path[0]]
            current.key = block.heading_path[0]

        group = groups.setdefault(current.key, current)
        group.blocks.append(block)

    return groups


def _manual_children(
    document: ParsedDocument,
    parent,
    blocks: list[Block],
    config: ChunkerConfig,
    template: str,
):
    children = []
    current: list[Block] = []
    procedure: list[Block] = []

    def flush_current() -> None:
        nonlocal current
        if current:
            children.append(
                make_child_from_blocks(
                    document,
                    template,
                    parent,
                    len(children),
                    current,
                    chunk_type="manual_text",
                    heading_path=parent.heading_path,
                    metadata={"template_rule": "manual_text"},
                )
            )
        current = []

    def flush_procedure() -> None:
        nonlocal procedure
        if procedure:
            children.append(
                make_child_from_blocks(
                    document,
                    template,
                    parent,
                    len(children),
                    procedure,
                    chunk_type="manual_procedure",
                    heading_path=parent.heading_path,
                    metadata={"template_rule": "manual_procedure"},
                )
            )
        procedure = []

    for block in blocks:
        first = _first_line(block)
        if block.block_type in MEDIA_TYPES:
            flush_current()
            flush_procedure()
            child_type = (
                "manual_troubleshooting_table"
                if block.block_type == "table" and _TROUBLE_RE.search(block.text)
                else None
            )
            children.append(
                make_child_from_blocks(
                    document,
                    template,
                    parent,
                    len(children),
                    [block],
                    chunk_type=child_type,
                    heading_path=parent.heading_path,
                    metadata={"template_rule": "manual_media_independent"},
                )
            )
            continue

        if _CALLOUT_RE.match(first):
            flush_current()
            flush_procedure()
            children.append(
                make_child_from_blocks(
                    document,
                    template,
                    parent,
                    len(children),
                    [block],
                    chunk_type="manual_callout",
                    heading_path=parent.heading_path,
                    metadata={"template_rule": "manual_callout"},
                )
            )
            continue

        if block.block_type == "list_item" or _PROCEDURE_RE.match(first):
            flush_current()
            procedure.append(block)
            continue

        if procedure:
            flush_procedure()
        if current and token_count([*current, block]) > config.child_max_tokens:
            flush_current()
        current.append(block)

    flush_current()
    flush_procedure()
    return children


def _first_line(block: Block) -> str:
    return next((line.strip() for line in block.text.splitlines() if line.strip()), "")
