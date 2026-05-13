"""Attach text context around table and figure chunks."""
from __future__ import annotations

from recallforge.chunking.chunkers.base import ChunkerConfig
from recallforge.chunking.ir.models import Block, ChildChunk, ParsedDocument

MEDIA_CHUNK_TYPES = {"table", "figure", "caption"}
SKIP_CONTEXT_TYPES = {"header", "footer", "page_number", "table", "figure"}


def attach_media_context(
    child_chunks: list[ChildChunk],
    document: ParsedDocument,
    config: ChunkerConfig,
) -> None:
    blocks_by_id = {block.block_id: block for block in document.blocks}
    ordered_blocks = sorted(document.blocks, key=lambda b: (b.page_number, b.reading_order))

    for child in child_chunks:
        if child.chunk_type not in MEDIA_CHUNK_TYPES or not child.source_block_ids:
            continue
        source = blocks_by_id.get(child.source_block_ids[0])
        if source is None:
            continue
        window = config.table_context_blocks if child.chunk_type == "table" else config.image_context_blocks
        caption, caption_ids = _caption_context(ordered_blocks, source)
        before, before_ids = _neighbor_texts(ordered_blocks, source, -1, window)
        after, after_ids = _neighbor_texts(ordered_blocks, source, 1, window)
        if caption:
            before = "\n\n".join(part for part in (caption, before) if part)
            before_ids = [*caption_ids, *before_ids]
        child.context_before = before or None
        child.context_after = after or None
        child.metadata["context_block_ids_before"] = before_ids
        child.metadata["context_block_ids_after"] = after_ids
        child.metadata["context_caption_block_ids"] = caption_ids
        child.metadata["context_strategy"] = (
            "caption_and_same_section_neighbors" if caption else "same_section_neighbors"
        )


def _neighbor_texts(
    ordered_blocks: list[Block],
    source: Block,
    direction: int,
    limit: int,
) -> tuple[str, list[str]]:
    try:
        source_index = ordered_blocks.index(source)
    except ValueError:
        return "", []

    texts: list[str] = []
    ids: list[str] = []
    cursor = source_index + direction

    while 0 <= cursor < len(ordered_blocks) and len(texts) < limit:
        block = ordered_blocks[cursor]
        cursor += direction
        if block.section_id != source.section_id:
            break
        if block.block_type in SKIP_CONTEXT_TYPES:
            continue
        if not block.text.strip():
            continue
        texts.append(block.text.strip())
        ids.append(block.block_id)

    if direction < 0:
        texts.reverse()
        ids.reverse()
    return "\n\n".join(texts), ids


def _caption_context(ordered_blocks: list[Block], source: Block) -> tuple[str, list[str]]:
    texts: list[str] = []
    ids: list[str] = []
    if source.caption:
        texts.append(source.caption.strip())
        ids.append(source.block_id)

    try:
        source_index = ordered_blocks.index(source)
    except ValueError:
        return "\n\n".join(texts), ids

    for offset in (-1, 1):
        idx = source_index + offset
        if idx < 0 or idx >= len(ordered_blocks):
            continue
        block = ordered_blocks[idx]
        if block.page_number != source.page_number:
            continue
        if block.block_type != "caption":
            continue
        if block.text.strip() and block.block_id not in ids:
            texts.append(block.text.strip())
            ids.append(block.block_id)

    return "\n\n".join(texts), ids
