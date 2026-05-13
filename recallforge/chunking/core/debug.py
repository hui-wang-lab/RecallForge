"""Debug payload builders for parser/chunker observability."""
from __future__ import annotations

from collections import Counter
from typing import Any

from recallforge.chunking.ir.models import ChunkPackage, ParsedDocument


def build_debug_payload(document: ParsedDocument, package: ChunkPackage) -> dict[str, Any]:
    parent_by_id = {parent.parent_id: parent for parent in package.parent_chunks}
    return {
        "section_tree": [section.to_dict() for section in document.section_tree],
        "block_summary": {
            "total": len(document.blocks),
            "by_type": dict(Counter(block.block_type for block in document.blocks)),
            "with_bbox": sum(1 for block in document.blocks if block.bbox is not None),
            "with_section": sum(1 for block in document.blocks if block.section_id),
        },
        "chunk_summary": {
            "parent_count": len(package.parent_chunks),
            "child_count": len(package.child_chunks),
            "child_by_type": dict(Counter(child.chunk_type for child in package.child_chunks)),
        },
        "parent_child_graph": [
            {
                "parent_id": parent.parent_id,
                "title": parent.title,
                "page_span": list(parent.page_span),
                "child_count": len(parent.child_chunk_ids),
                "child_chunk_ids": list(parent.child_chunk_ids),
            }
            for parent in package.parent_chunks
        ],
        "orphan_child_ids": [
            child.chunk_id
            for child in package.child_chunks
            if child.parent_id not in parent_by_id
        ],
        "warnings": list(package.warnings),
    }

