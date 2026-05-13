"""Shared helpers for template chunkers."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from recallforge.chunking.core.ids import child_id, parent_id, stable_hash
from recallforge.chunking.ir.models import BBoxRef, Block, ChildChunk, ParentChunk, ParsedDocument
from recallforge.chunking.tokenizer import estimate_tokens

MEDIA_TYPES = {"table", "figure", "caption"}
SKIP_TYPES = {"header", "footer", "page_number"}


@dataclass
class BlockGroup:
    key: str
    title: str
    heading_path: list[str]
    blocks: list[Block] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def ordered_content_blocks(document: ParsedDocument) -> list[Block]:
    return [
        block
        for block in sorted(document.blocks, key=lambda b: (b.page_number, b.reading_order))
        if block.block_type not in SKIP_TYPES and block.text.strip()
    ]


def make_parent_from_blocks(
    document: ParsedDocument,
    group: BlockGroup,
) -> ParentChunk:
    pages = [block.page_number for block in group.blocks] or [1]
    section_key = stable_hash("template-section", document.document_id, group.key)
    heading_path = group.heading_path or [group.title or "Document"]
    return ParentChunk(
        parent_id=parent_id(document.document_id, section_key),
        document_id=document.document_id,
        section_id=section_key,
        heading_path=heading_path,
        title=group.title or heading_path[-1],
        text="\n\n".join(block.markdown or block.text for block in group.blocks if block.text.strip()),
        page_span=(min(pages), max(pages)),
        source_block_ids=[block.block_id for block in group.blocks],
        metadata=dict(group.metadata),
    )


def make_child_from_blocks(
    document: ParsedDocument,
    template: str,
    parent: ParentChunk,
    child_index: int,
    blocks: list[Block],
    *,
    chunk_type: str | None = None,
    heading_path: list[str] | None = None,
    metadata: dict | None = None,
    text_prefix: str | None = None,
) -> ChildChunk:
    pages = [block.page_number for block in blocks] or [1]
    source_block_ids = [block.block_id for block in blocks]
    text = chunk_text(blocks, heading_path=heading_path, text_prefix=text_prefix)
    return ChildChunk(
        chunk_id=child_id(document.document_id, template, parent.parent_id, child_index, source_block_ids),
        parent_id=parent.parent_id,
        document_id=document.document_id,
        chunk_type=chunk_type or infer_chunk_type(blocks),
        template=template,
        text=text,
        page_span=(min(pages), max(pages)),
        source_block_ids=source_block_ids,
        bbox_refs=[
            BBoxRef(block_id=block.block_id, page_number=block.page_number, bbox=block.bbox)
            for block in blocks
            if block.bbox is not None
        ],
        heading_path=list(heading_path if heading_path is not None else (blocks[0].heading_path if blocks else [])),
        token_count=estimate_tokens(text),
        metadata={
            "block_types": [block.block_type for block in blocks],
            **dict(metadata or {}),
        },
    )


def chunk_text(
    blocks: list[Block],
    *,
    heading_path: list[str] | None = None,
    text_prefix: str | None = None,
) -> str:
    path = heading_path if heading_path is not None else (blocks[0].heading_path if blocks else [])
    body = "\n\n".join(block.markdown or block.text for block in blocks if block.text.strip())
    parts: list[str] = []
    if path:
        deepest = path[-1].strip()
        if deepest and body.startswith(deepest):
            # Body already opens with the most specific heading (e.g. a legal article whose
            # full text became the active_article label); only prepend the parent path.
            parent_heading = " > ".join(path[:-1])
            if parent_heading:
                parts.append(parent_heading)
        else:
            heading = " > ".join(path)
            if heading and not body.startswith(heading):
                parts.append(heading)
    if text_prefix and text_prefix not in body:
        parts.append(text_prefix)
    parts.append(body)
    return "\n\n".join(part.strip() for part in parts if part and part.strip()).strip()


def infer_chunk_type(blocks: list[Block]) -> str:
    if len(blocks) == 1 and blocks[0].block_type in MEDIA_TYPES:
        return blocks[0].block_type
    if all(block.block_type == "list_item" for block in blocks):
        return "list"
    return "text"


def group_blocks_by_heading(
    blocks: list[Block],
    *,
    default_title: str = "Document",
) -> "OrderedDict[str, BlockGroup]":
    groups: "OrderedDict[str, BlockGroup]" = OrderedDict()
    current_key = default_title
    current_title = default_title
    current_path = [default_title]

    for block in blocks:
        if block.heading_path:
            current_path = [block.heading_path[0]]
            current_title = current_path[0]
            current_key = current_title
        group = groups.setdefault(
            current_key,
            BlockGroup(key=current_key, title=current_title, heading_path=list(current_path)),
        )
        group.blocks.append(block)
    return groups


def token_count(blocks: list[Block]) -> int:
    return estimate_tokens("\n\n".join(block.markdown or block.text for block in blocks))

