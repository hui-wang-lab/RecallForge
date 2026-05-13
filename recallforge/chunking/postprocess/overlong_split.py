"""Post-processing pass to split oversized child chunks."""
from __future__ import annotations

import re
from dataclasses import replace

from recallforge.chunking.ir.models import ChildChunk, ChunkPackage
from recallforge.chunking.tokenizer import estimate_tokens

PROTECTED_TYPES = {"table", "figure", "caption", "image_context", "table_row_group"}
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])\s+|(?<=[。！？!?；;])")
_FALLBACK_WORD_BATCH = 160


def split_overlong_chunks(package: ChunkPackage, max_tokens: int | None = None) -> list[str]:
    """Split non-media child chunks that exceed the configured token budget."""
    limit = max_tokens if max_tokens is not None else _limit(package)
    if not limit or limit <= 0:
        return []

    warnings: list[str] = []
    new_children: list[ChildChunk] = []
    replacements: dict[str, list[str]] = {}

    for child in package.child_chunks:
        if child.chunk_type in PROTECTED_TYPES or child.token_count <= limit:
            new_children.append(child)
            continue

        heading = _heading_prefix(child)
        body = _strip_heading_prefix(child.text, heading)
        part_limit = _body_limit(limit, heading)
        parts = _split_text(body, part_limit)
        if len(parts) <= 1:
            new_children.append(child)
            continue

        split_children: list[ChildChunk] = []
        for index, text in enumerate(parts, start=1):
            part_text = _with_heading(text, heading)
            split_child = replace(
                child,
                chunk_id=f"{child.chunk_id}:part{index}",
                text=part_text,
                token_count=estimate_tokens(part_text),
                metadata={
                    **child.metadata,
                    "overlong_split": True,
                    "split_part_index": index,
                    "split_part_count": len(parts),
                    "original_chunk_id": child.chunk_id,
                },
            )
            split_children.append(split_child)

        new_children.extend(split_children)
        replacements[child.chunk_id] = [part.chunk_id for part in split_children]
        warnings.append(
            f"[overlong_split] split {child.chunk_id} into {len(split_children)} parts "
            f"with limit {limit} tokens"
        )

    if not replacements:
        return warnings

    for parent in package.parent_chunks:
        updated: list[str] = []
        for child_id in parent.child_chunk_ids:
            updated.extend(replacements.get(child_id, [child_id]))
        parent.child_chunk_ids = updated

    package.child_chunks = new_children
    return warnings


def _limit(package: ChunkPackage) -> int | None:
    value = (package.metadata.get("chunker_config") or {}).get("child_max_tokens")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _heading_prefix(child: ChildChunk) -> str:
    return " > ".join(part.strip() for part in child.heading_path if part and part.strip())


def _strip_heading_prefix(text: str, heading: str) -> str:
    stripped = text.strip()
    if not heading or not stripped.startswith(heading):
        return stripped
    return stripped[len(heading):].lstrip()


def _with_heading(text: str, heading: str) -> str:
    body = text.strip()
    if not heading or body.startswith(heading):
        return body
    return f"{heading}\n\n{body}".strip()


def _body_limit(limit: int, heading: str) -> int:
    if not heading:
        return limit
    return max(1, limit - estimate_tokens(heading) - 2)


def _split_text(text: str, limit: int) -> list[str]:
    units = _text_units(text)
    parts: list[str] = []
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        if current:
            parts.append("".join(current).strip())
            current = []

    for unit in units:
        if not unit.strip():
            continue
        if estimate_tokens(unit) > limit:
            flush()
            parts.extend(_hard_split(unit, limit))
            continue
        candidate = "".join([*current, unit])
        if current and estimate_tokens(candidate) > limit:
            flush()
        current.append(unit)

    flush()
    return [part for part in parts if part]


def _text_units(text: str) -> list[str]:
    paragraphs = re.split(r"(\n{2,})", text)
    units: list[str] = []
    for paragraph in paragraphs:
        if not paragraph:
            continue
        if paragraph.startswith("\n"):
            if units:
                units[-1] += paragraph
            continue
        sentences = [part for part in _SENTENCE_SPLIT_RE.split(paragraph) if part]
        units.extend(sentences or [paragraph])
    return units


def _hard_split(text: str, limit: int) -> list[str]:
    words = text.split()
    if len(words) > 1:
        parts: list[str] = []
        current: list[str] = []
        for word in words:
            candidate = " ".join([*current, word])
            if current and estimate_tokens(candidate) > limit:
                parts.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            parts.append(" ".join(current))
        return parts

    out: list[str] = []
    step = max(1, min(len(text), _FALLBACK_WORD_BATCH))
    cursor = 0
    while cursor < len(text):
        chunk = text[cursor: cursor + step]
        while estimate_tokens(chunk) > limit and step > 1:
            step = max(1, step // 2)
            chunk = text[cursor: cursor + step]
        out.append(chunk)
        cursor += len(chunk)
    return out
