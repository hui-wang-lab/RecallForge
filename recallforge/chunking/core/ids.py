"""Stable identifier helpers for IR blocks and chunks."""
from __future__ import annotations

import hashlib


def stable_hash(*parts: object, length: int = 32) -> str:
    canonical = ":".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:length]


def block_id(document_id: str, page_number: int, reading_order: int, block_type: str) -> str:
    return stable_hash("block", document_id, page_number, reading_order, block_type)


def section_id(document_id: str, heading_path: list[str]) -> str:
    key = " > ".join(heading_path) if heading_path else "Document"
    return stable_hash("section", document_id, key)


def parent_id(document_id: str, section_id_value: str) -> str:
    return stable_hash("parent", document_id, section_id_value)


def child_id(
    document_id: str,
    template: str,
    parent_id_value: str,
    child_index: int,
    source_block_ids: list[str],
) -> str:
    source_key = ",".join(source_block_ids)
    return stable_hash("child", document_id, template, parent_id_value, child_index, source_key)

