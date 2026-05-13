"""Build ChunkFlow pipeline config from RecallForge settings."""

from __future__ import annotations

from recallforge.chunking.core.pipeline import PipelineConfig
from recallforge.config import Settings


def build_pipeline_config(
    settings: Settings,
    *,
    parser_hint: str = "auto",
    template_hint: str = "auto",
) -> PipelineConfig:
    return PipelineConfig(
        parser=parser_hint,
        template=template_hint,
        child_max_tokens=settings.child_max_tokens,
        child_min_tokens=settings.child_min_tokens,
        parent_granularity=settings.parent_granularity,
        include_blocks=True,
    )
