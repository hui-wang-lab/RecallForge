"""Picture/scanned PDF chunker."""
from __future__ import annotations

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


class PicturePdfChunker(TemplateChunker):
    name = "picture_pdf"

    def chunk(self, document: ParsedDocument, config: ChunkerConfig) -> ChunkingResult:
        groups = _page_groups(ordered_content_blocks(document))
        parents = []
        children = []

        for group in groups.values():
            parent = make_parent_from_blocks(document, group)
            local = _page_children(document, parent, group.blocks, config, self.name)
            parent.child_chunk_ids = [child.chunk_id for child in local]
            parents.append(parent)
            children.extend(local)

        return ChunkingResult(chunker_used=self.name, parent_chunks=parents, child_chunks=children)


def _page_groups(blocks: list[Block]) -> "OrderedDict[str, BlockGroup]":
    groups: "OrderedDict[str, BlockGroup]" = OrderedDict()
    for block in blocks:
        key = f"Page {block.page_number}"
        group = groups.setdefault(
            key,
            BlockGroup(
                key=key,
                title=key,
                heading_path=[key],
                metadata={"template_rule": "picture_pdf_page", "page_number": block.page_number},
            ),
        )
        group.blocks.append(block)
    return groups


def _page_children(
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
                    chunk_type="ocr_text",
                    heading_path=parent.heading_path,
                    metadata={"template_rule": "picture_pdf_ocr_text"},
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
                    chunk_type="image_context",
                    heading_path=parent.heading_path,
                    metadata={"template_rule": "picture_pdf_image_or_caption"},
                )
            )
            continue
        if current and token_count([*current, block]) > config.child_max_tokens:
            flush()
        current.append(block)

    flush()
    return children
