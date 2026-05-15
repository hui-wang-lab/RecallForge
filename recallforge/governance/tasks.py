"""M6 governance task command records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReindexPlan:
    knowledge_base_id: int
    dry_run: bool
    document_ids: list[int] = field(default_factory=list)
    estimated_documents: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
