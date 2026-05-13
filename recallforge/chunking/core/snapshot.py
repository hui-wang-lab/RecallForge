"""Stable package snapshots for golden regression tests."""
from __future__ import annotations

from typing import Any

from recallforge.chunking.ir.models import ChunkPackage


def package_snapshot(package: ChunkPackage) -> dict[str, Any]:
    """Return a stable, ID-light summary suitable for golden tests."""
    metrics = package.parse_report.metrics
    return {
        "document_type": package.document_type,
        "parser_used": package.parser_used,
        "chunker_used": package.chunker_used,
        "counts": {
            "parents": len(package.parent_chunks),
            "children": len(package.child_chunks),
            "blocks": len(package.blocks),
            "warnings": len(package.warnings),
        },
        "parse_report": {
            "page_count": package.parse_report.page_count,
            "block_count": package.parse_report.block_count,
            "table_count": package.parse_report.table_count,
            "figure_count": package.parse_report.figure_count,
        },
        "metrics": {
            "orphan_child_count": metrics.get("orphan_child_count"),
            "chunks_without_source_block_count": metrics.get("chunks_without_source_block_count"),
            "over_max_token_child_count": metrics.get("over_max_token_child_count"),
            "split_overlong_child_count": metrics.get("split_overlong_child_count"),
            "parent_child_edge_count": metrics.get("parent_child_edge_count"),
            "child_type_counts": _sorted_dict(metrics.get("child_type_counts", {})),
            "block_type_counts": _sorted_dict(metrics.get("block_type_counts", {})),
            "layout_noise_removed_count": metrics.get("layout_noise_removed_count"),
            "inferred_heading_count": metrics.get("inferred_heading_count"),
            "heading_level_counts": _sorted_dict(metrics.get("heading_level_counts", {})),
            "boundary_repair_count": metrics.get("boundary_repair_count"),
            "media_context_strategy_counts": _sorted_dict(metrics.get("media_context_strategy_counts", {})),
        },
        "parents": [
            {
                "title": parent.title,
                "child_count": len(parent.child_chunk_ids),
                "page_span": list(parent.page_span),
            }
            for parent in package.parent_chunks
        ],
        "children": [
            {
                "chunk_type": child.chunk_type,
                "heading_path": list(child.heading_path),
                "page_span": list(child.page_span),
                "source_blocks": len(child.source_block_ids),
                "token_count": child.token_count,
                "metadata_keys": sorted(child.metadata.keys()),
                "text_preview": _preview(child.text),
            }
            for child in package.child_chunks
        ],
    }


def _sorted_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in sorted(value)}


def _preview(text: str, limit: int = 120) -> str:
    return " ".join(text.split())[:limit]
