"""Post-processing passes for chunk packages."""

from recallforge.chunking.postprocess.boundary_repair import repair_boundaries
from recallforge.chunking.postprocess.media_context import attach_media_context
from recallforge.chunking.postprocess.quality import add_quality_metrics
from recallforge.chunking.postprocess.small_chunk_merge import merge_small_chunks

__all__ = [
    "attach_media_context",
    "add_quality_metrics",
    "repair_boundaries",
    "merge_small_chunks",
]
