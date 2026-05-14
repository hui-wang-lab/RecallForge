"""Generic structure-aware parent-child chunker."""
from __future__ import annotations

from collections import OrderedDict

from recallforge.chunking.chunkers.base import ChunkerConfig, ChunkingResult, TemplateChunker
from recallforge.chunking.core.ids import child_id, parent_id
from recallforge.chunking.ir.models import BBoxRef, Block, ChildChunk, ParentChunk, ParsedDocument, SectionNode
from recallforge.chunking.tokenizer import estimate_tokens

MEDIA_TYPES = {"table", "figure", "caption"}
SKIP_TYPES = {"header", "footer", "page_number"}

_GRANULARITY_LEVELS: dict[str, int] = {"chapter": 1, "section": 2}


class GenericStructuredChunker(TemplateChunker):
    name = "generic_structured"

    def chunk(self, document: ParsedDocument, config: ChunkerConfig) -> ChunkingResult:
        sections_by_id = {s.section_id: s for s in document.section_tree}
        parent_groups = _parent_groups(document, config)
        parent_chunks: list[ParentChunk] = []
        child_chunks: list[ChildChunk] = []
        warnings: list[str] = []

        for section_key, blocks in parent_groups.items():
            if not blocks:
                continue
            parent = _make_parent(document, section_key, blocks, sections_by_id)
            local_children = self._make_children(document, parent, blocks, config)
            parent.child_chunk_ids = [child.chunk_id for child in local_children]
            parent_chunks.append(parent)
            child_chunks.extend(local_children)

        if not parent_chunks and document.blocks:
            warnings.append("No parent chunks were generated from parsed blocks.")

        return ChunkingResult(
            chunker_used=self.name,
            parent_chunks=parent_chunks,
            child_chunks=child_chunks,
            warnings=warnings,
        )

    def _make_children(
        self,
        document: ParsedDocument,
        parent: ParentChunk,
        blocks: list[Block],
        config: ChunkerConfig,
    ) -> list[ChildChunk]:
        children: list[ChildChunk] = []
        current: list[Block] = []

        def flush() -> None:
            nonlocal current
            if current:
                children.append(_make_child(document, self.name, parent, len(children), current))
                current = []

        for block in blocks:
            if block.block_type in SKIP_TYPES or not block.text.strip():
                continue
            if block.block_type in MEDIA_TYPES:
                flush()
                children.append(_make_child(document, self.name, parent, len(children), [block]))
                continue

            candidate = [*current, block]
            if current and _token_count(candidate) > config.child_max_tokens:
                flush()
            current.append(block)

        flush()
        return children


def _parent_groups(document: ParsedDocument, config: ChunkerConfig) -> "OrderedDict[str, list[Block]]":
    max_level = _GRANULARITY_LEVELS.get(config.parent_granularity, 1)
    sections_by_path: dict[tuple[str, ...], SectionNode] = {
        tuple(s.heading_path): s for s in document.section_tree
    }
    groups: "OrderedDict[str, list[Block]]" = OrderedDict()
    for block in sorted(document.blocks, key=lambda b: (b.page_number, b.reading_order)):
        section = _section_for_block(block, document, sections_by_path, max_level)
        key = section.section_id if section else "document"
        groups.setdefault(key, []).append(block)
    return groups


def _section_for_block(
    block: Block,
    document: ParsedDocument,
    sections_by_path: dict[tuple[str, ...], SectionNode],
    max_level: int,
) -> SectionNode | None:
    if not block.heading_path:
        return document.section_tree[0] if document.section_tree else None
    # Find the deepest section at or above max_level that exists in the tree
    target_depth = min(max_level, len(block.heading_path))
    for depth in range(target_depth, 0, -1):
        section = sections_by_path.get(tuple(block.heading_path[:depth]))
        if section is not None:
            return section
    return document.section_tree[0] if document.section_tree else None


def _make_parent(
    document: ParsedDocument,
    section_key: str,
    blocks: list[Block],
    sections_by_id: dict[str, SectionNode],
) -> ParentChunk:
    section = sections_by_id.get(section_key)
    if section:
        heading_path = list(section.heading_path)
        title = section.title
    else:
        heading_path = blocks[0].heading_path if blocks and blocks[0].heading_path else ["Document"]
        title = heading_path[0] if heading_path else "Document"
    pages = [block.page_number for block in blocks]
    text = "\n\n".join(block.text for block in blocks if block.text.strip())
    source_block_ids = [block.block_id for block in blocks]
    return ParentChunk(
        parent_id=parent_id(document.document_id, section_key),
        document_id=document.document_id,
        section_id=section_key,
        heading_path=heading_path,
        title=title,
        text=text,
        page_span=(min(pages), max(pages)),
        source_block_ids=source_block_ids,
        metadata={"token_count": estimate_tokens(text)},
    )


def _make_child(
    document: ParsedDocument,
    template: str,
    parent: ParentChunk,
    child_index: int,
    blocks: list[Block],
) -> ChildChunk:
    pages = [block.page_number for block in blocks]
    source_block_ids = [block.block_id for block in blocks]
    chunk_type = _chunk_type(blocks)
    text = _chunk_text(blocks)
    return ChildChunk(
        chunk_id=child_id(document.document_id, template, parent.parent_id, child_index, source_block_ids),
        parent_id=parent.parent_id,
        document_id=document.document_id,
        chunk_type=chunk_type,
        template=template,
        text=text,
        page_span=(min(pages), max(pages)),
        source_block_ids=source_block_ids,
        bbox_refs=[
            BBoxRef(block_id=block.block_id, page_number=block.page_number, bbox=block.bbox)
            for block in blocks
            if block.bbox is not None
        ],
        heading_path=list(blocks[0].heading_path),
        token_count=estimate_tokens(text),
        metadata={"block_types": [block.block_type for block in blocks]},
    )


def _chunk_text(blocks: list[Block]) -> str:
    heading_path = blocks[0].heading_path if blocks else []
    body = "\n\n".join(block.markdown or block.text for block in blocks if block.text.strip())
    if not heading_path:
        return body.strip()
    deepest = heading_path[-1].strip()
    # If the body already opens with the deepest heading component (e.g. a legal article whose
    # full text was promoted to a section node), only prepend the parent path to avoid duplication.
    if deepest and body.startswith(deepest):
        parent_heading = " > ".join(heading_path[:-1])
        return f"{parent_heading}\n\n{body}".strip() if parent_heading else body.strip()
    heading = " > ".join(heading_path)
    if heading and not body.startswith(heading):
        return f"{heading}\n\n{body}".strip()
    return body.strip()


def _chunk_type(blocks: list[Block]) -> str:
    if len(blocks) == 1 and blocks[0].block_type in MEDIA_TYPES:
        return blocks[0].block_type
    if all(block.block_type == "list_item" for block in blocks):
        return "list"
    return "text"


def _token_count(blocks: list[Block]) -> int:
    return estimate_tokens("\n\n".join(block.markdown or block.text for block in blocks))
