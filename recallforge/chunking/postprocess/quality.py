"""Quality metrics for chunk packages."""
from __future__ import annotations

from collections import Counter

from recallforge.chunking.ir.models import ChunkPackage


def add_quality_metrics(package: ChunkPackage) -> None:
    child_count = len(package.child_chunks)
    parent_ids = {parent.parent_id for parent in package.parent_chunks}
    child_max_tokens = _child_max_tokens(package)
    package.parse_report.metrics.update(
        {
            "parent_count": len(package.parent_chunks),
            "child_count": child_count,
            "orphan_child_count": sum(
                1
                for child in package.child_chunks
                if child.parent_id not in parent_ids
            ),
            "chunks_without_source_block_count": sum(
                1 for child in package.child_chunks if not child.source_block_ids
            ),
            "chunks_with_bbox_refs_count": sum(1 for child in package.child_chunks if child.bbox_refs),
            "over_max_token_child_count": sum(
                1 for child in package.child_chunks if child_max_tokens and child.token_count > child_max_tokens
            ),
            "split_overlong_child_count": sum(
                1 for child in package.child_chunks if child.metadata.get("overlong_split")
            ),
            "parent_child_edge_count": sum(len(parent.child_chunk_ids) for parent in package.parent_chunks),
            "block_type_counts": dict(Counter(block.block_type for block in package.blocks)),
            "child_type_counts": dict(Counter(child.chunk_type for child in package.child_chunks)),
            "layout_noise_removed_count": package.metadata.get("layout_noise_removed_count", 0),
            "inferred_heading_count": sum(
                1
                for block in package.blocks
                if str(block.metadata.get("heading_reason", "")).endswith("_pattern")
            ),
            "heading_level_counts": dict(
                Counter(
                    str(block.metadata.get("heading_level"))
                    for block in package.blocks
                    if block.metadata.get("heading_level") is not None
                )
            ),
            "boundary_repair_count": sum(
                1 for warning in package.warnings if warning.startswith("[boundary_repair]")
            ),
            "media_context_strategy_counts": dict(
                Counter(
                    str(child.metadata.get("context_strategy"))
                    for child in package.child_chunks
                    if child.metadata.get("context_strategy")
                )
            ),
            "table_context_coverage": _table_context_coverage(package),
            "figure_context_coverage": _media_context_coverage(package, {"figure", "image_context"}),
            "avg_tokens_per_child": _avg_tokens(package),
            "max_tokens_per_child": max((child.token_count for child in package.child_chunks), default=0),
            "min_tokens_per_child": min((child.token_count for child in package.child_chunks), default=0),
        }
    )


def _table_context_coverage(package: ChunkPackage) -> float:
    table_chunks = [child for child in package.child_chunks if child.chunk_type == "table"]
    if not table_chunks:
        return 1.0
    covered = sum(1 for child in table_chunks if child.context_before or child.context_after)
    return covered / len(table_chunks)


def _media_context_coverage(package: ChunkPackage, chunk_types: set[str]) -> float:
    chunks = [child for child in package.child_chunks if child.chunk_type in chunk_types]
    if not chunks:
        return 1.0
    covered = sum(1 for child in chunks if child.context_before or child.context_after)
    return covered / len(chunks)


def _avg_tokens(package: ChunkPackage) -> float:
    if not package.child_chunks:
        return 0.0
    return sum(child.token_count for child in package.child_chunks) / len(package.child_chunks)


def _child_max_tokens(package: ChunkPackage) -> int | None:
    value = (package.metadata.get("chunker_config") or {}).get("child_max_tokens")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
