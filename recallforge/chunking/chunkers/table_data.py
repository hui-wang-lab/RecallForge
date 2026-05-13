"""Structured table data chunker."""
from __future__ import annotations

from collections import OrderedDict

from recallforge.chunking.chunkers.base import ChunkerConfig, ChunkingResult, TemplateChunker
from recallforge.chunking.chunkers.template_utils import (
    BlockGroup,
    make_child_from_blocks,
    make_parent_from_blocks,
    ordered_content_blocks,
    token_count,
)
from recallforge.chunking.ir.models import Block, ParsedDocument


class TableDataChunker(TemplateChunker):
    name = "table_data"

    def chunk(self, document: ParsedDocument, config: ChunkerConfig) -> ChunkingResult:
        groups = _sheet_groups(ordered_content_blocks(document))
        parents = []
        children = []

        for group in groups.values():
            parent = make_parent_from_blocks(document, group)
            local = _row_group_children(document, parent, group.blocks, config, self.name)
            parent.child_chunk_ids = [child.chunk_id for child in local]
            parents.append(parent)
            children.extend(local)

        return ChunkingResult(chunker_used=self.name, parent_chunks=parents, child_chunks=children)


def _sheet_groups(blocks: list[Block]) -> "OrderedDict[str, BlockGroup]":
    groups: "OrderedDict[str, BlockGroup]" = OrderedDict()
    for block in blocks:
        sheet = str(block.metadata.get("sheet_name") or block.metadata.get("table_name") or "Table")
        group = groups.setdefault(
            sheet,
            BlockGroup(
                key=sheet,
                title=sheet,
                heading_path=[sheet],
                metadata={"template_rule": "table_data_sheet", "sheet_name": sheet},
            ),
        )
        group.blocks.append(block)
    return groups


def _row_group_children(
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
            row_indexes = [block.metadata.get("row_index") for block in current if "row_index" in block.metadata]
            metadata = {
                "template_rule": "table_data_row_group",
                "row_start": min(row_indexes) if row_indexes else None,
                "row_end": max(row_indexes) if row_indexes else None,
                "columns": current[0].metadata.get("columns", []),
                "sheet_name": current[0].metadata.get("sheet_name"),
            }
            children.append(
                make_child_from_blocks(
                    document,
                    template,
                    parent,
                    len(children),
                    current,
                    chunk_type="table_row_group",
                    heading_path=parent.heading_path,
                    metadata=metadata,
                )
            )
        current = []

    for block in blocks:
        if current and token_count([*current, block]) > config.child_max_tokens:
            flush()
        current.append(block)

    flush()
    return children
