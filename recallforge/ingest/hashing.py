"""Content hash helpers for ingest idempotency."""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

from recallforge.chunking.ir.models import Block, ChunkPackage

_BLANK_LINES_RE = re.compile(r"\n{3,}")


def normalize_content(text: str) -> str:
    """Normalize parsed text before hashing without changing semantic case."""
    normalized = text.replace("\ufeff", "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    normalized = _BLANK_LINES_RE.sub("\n\n", normalized)
    return normalized.strip()


def compute_content_hash(canonical_text: str) -> str:
    """Return a 64-char lowercase SHA-256 hex digest for normalized content."""
    normalized = normalize_content(canonical_text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def canonical_text_from_blocks(blocks: Iterable[Block]) -> str:
    ordered = sorted(blocks, key=lambda b: (b.page_number, b.reading_order, b.block_id))
    return "\n\n".join(block.text for block in ordered if block.text.strip())


def compute_package_content_hash(package: ChunkPackage) -> str:
    """Hash the parsed document content represented by a ChunkPackage."""
    if package.blocks:
        return compute_content_hash(canonical_text_from_blocks(package.blocks))
    if package.parent_chunks:
        return compute_content_hash("\n\n".join(parent.text for parent in package.parent_chunks))
    from recallforge.ingest.errors import IngestError

    raise IngestError("Document produced no parseable content; cannot compute content hash")
