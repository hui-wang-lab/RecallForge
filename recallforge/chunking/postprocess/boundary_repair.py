"""Post-processing pass to repair cross-page boundary breaks in child chunks."""
from __future__ import annotations

import re

from recallforge.chunking.ir.models import ChildChunk, ChunkPackage, ParentChunk
from recallforge.chunking.tokenizer import estimate_tokens

_CHINESE_ORPHAN_PUNCT = re.compile(r'^[，。；：、！？…—]+')
_EN_TERMINAL = re.compile(r'[.!?""»」』]\s*$')
_LIST_START = re.compile(r'^\s*(?:[-*•·]|\d+[.)。]|[①-⑩]|[（(]\d+[）)])\s+')
_NUMBERED_LIST_START = re.compile(r'^\s*(\d+)[.)、]\s+')
_SOFT_TERMINAL = set(",，、;；:：")

PROTECTED_TYPES = {"table", "figure", "caption", "image_context"}
TEXT_TYPES = {"text", "list", "paragraph", "prose"}


def repair_boundaries(package: ChunkPackage) -> list[str]:
    """Repair cross-page sentence/list breaks and orphan Chinese punctuation.

    Mutates package.child_chunks and parent.child_chunk_ids in-place.
    Returns informational messages about each repair made.
    """
    warnings: list[str] = []

    children_by_parent: dict[str, list[ChildChunk]] = {}
    for child in package.child_chunks:
        children_by_parent.setdefault(child.parent_id, []).append(child)

    to_remove: set[str] = set()

    for parent in package.parent_chunks:
        children = children_by_parent.get(parent.parent_id, [])
        if children:
            child_order = {cid: idx for idx, cid in enumerate(parent.child_chunk_ids)}
            children.sort(key=lambda c: child_order.get(c.chunk_id, len(parent.child_chunk_ids)))
        i = 0
        while i < len(children) - 1:
            curr = children[i]
            nxt = children[i + 1]

            if curr.chunk_type in PROTECTED_TYPES or nxt.chunk_type in PROTECTED_TYPES:
                i += 1
                continue

            repair = _detect_break(curr, nxt)
            if repair is None:
                i += 1
                continue

            if repair == "punct":
                m = _CHINESE_ORPHAN_PUNCT.match(nxt.text.lstrip())
                if m:
                    punct = m.group(0)
                    curr.text = curr.text.rstrip() + punct
                    remaining = nxt.text[nxt.text.index(punct) + len(punct):].lstrip()
                    nxt.text = remaining
                    warnings.append(
                        f"[boundary_repair] punct: moved '{punct}' from {nxt.chunk_id} → {curr.chunk_id}"
                    )
                    if not nxt.text.strip():
                        _absorb(parent, curr, nxt)
                        to_remove.add(nxt.chunk_id)
                        children.pop(i + 1)
                        continue  # recheck curr with new next
                i += 1
            else:
                _absorb(parent, curr, nxt, drop_trailing_hyphen=(repair == "hyphen"))
                to_remove.add(nxt.chunk_id)
                children.pop(i + 1)
                warnings.append(
                    f"[boundary_repair] {repair}: merged {nxt.chunk_id} → {curr.chunk_id}"
                )
                # don't advance i — check curr against the new next

    if to_remove:
        package.child_chunks = [c for c in package.child_chunks if c.chunk_id not in to_remove]

    warnings.extend(_repair_cross_parent_continuations(package))
    return warnings


def _repair_cross_parent_continuations(package: ChunkPackage) -> list[str]:
    warnings: list[str] = []
    if len(package.child_chunks) < 2:
        return warnings

    parent_map = {parent.parent_id: parent for parent in package.parent_chunks}
    to_remove: set[str] = set()
    i = 0
    while i < len(package.child_chunks) - 1:
        curr = package.child_chunks[i]
        nxt = package.child_chunks[i + 1]
        if curr.parent_id == nxt.parent_id:
            i += 1
            continue
        if curr.chunk_type in PROTECTED_TYPES or nxt.chunk_type in PROTECTED_TYPES:
            i += 1
            continue

        repair = _detect_break(curr, nxt)
        if repair is None or not _safe_cross_parent_merge(curr, nxt):
            i += 1
            continue

        source_parent = parent_map.get(nxt.parent_id)
        _absorb(parent_map.get(curr.parent_id), curr, nxt, drop_trailing_hyphen=(repair == "hyphen"))
        to_remove.add(nxt.chunk_id)
        if source_parent is not None:
            try:
                source_parent.child_chunk_ids.remove(nxt.chunk_id)
            except ValueError:
                pass
        warnings.append(f"[boundary_repair] cross_parent_{repair}: merged {nxt.chunk_id} → {curr.chunk_id}")
        package.child_chunks.pop(i + 1)

    if to_remove:
        empty_parent_ids = {
            parent.parent_id for parent in package.parent_chunks if not parent.child_chunk_ids
        }
        if empty_parent_ids:
            package.parent_chunks = [
                parent for parent in package.parent_chunks if parent.parent_id not in empty_parent_ids
            ]
    return warnings


def _detect_break(curr: ChildChunk, nxt: ChildChunk) -> str | None:
    curr_text = curr.text.rstrip()
    nxt_text = nxt.text.lstrip()
    if not curr_text or not nxt_text:
        return None

    # Chinese orphan punctuation — fix regardless of page position
    if _CHINESE_ORPHAN_PUNCT.match(nxt_text):
        return "punct"

    # Remaining repairs only apply across a page boundary
    if nxt.page_span[0] <= curr.page_span[0]:
        return None

    if curr_text.endswith("-") and nxt_text[:1].islower():
        return "hyphen"

    last_ch = curr_text[-1]
    ends_terminal = bool(_EN_TERMINAL.search(curr_text)) or last_ch in '。！？…—」』"'

    if not ends_terminal:
        first_ch = nxt_text[0] if nxt_text else ''
        # English lowercase continuation
        if first_ch.islower():
            return "sentence"
        # Chinese continuation (not a list header)
        if '一' <= first_ch <= '鿿' and not _LIST_START.match(nxt_text):
            return "sentence"
        if last_ch in _SOFT_TERMINAL:
            return "sentence"
        if _looks_like_page_continuation(curr, nxt):
            return "layout"

    # List continuation: nxt starts a list item and curr ends with one too
    if _LIST_START.match(nxt_text) and (
        _ends_with_list_item(curr_text) or _numbered_list_continues(curr_text, nxt_text)
    ):
        return "list"

    return None


def _safe_cross_parent_merge(curr: ChildChunk, nxt: ChildChunk) -> bool:
    if nxt.page_span[0] != curr.page_span[1] + 1:
        return False
    next_text = _strip_leading_running_headers(nxt.text).lstrip()
    if next_text != nxt.text:
        nxt.text = next_text
        nxt.token_count = estimate_tokens(nxt.text)
    first_line = next((line.strip() for line in next_text.splitlines() if line.strip()), "")
    if not first_line:
        return False
    if _looks_like_new_section(first_line):
        return False
    return bool(
        _CHINESE_ORPHAN_PUNCT.match(first_line)
        or first_line[:1].islower()
        or ('一' <= first_line[:1] <= '鿿' and not _LIST_START.match(first_line))
    )


def _strip_leading_running_headers(text: str) -> str:
    lines = text.splitlines()
    while lines:
        first = lines[0].strip()
        if not first:
            lines.pop(0)
            continue
        if _looks_like_running_header_line(first):
            lines.pop(0)
            continue
        break
    return "\n".join(lines).lstrip()


def _looks_like_running_header_line(line: str) -> bool:
    return bool(
        re.search(r"个人保险基本条款第[零一二三四五六七八九十\d]+版$", line)
        or re.search(r"^[^。！？；：]{4,80}(?:寿险|保险|险)\S*利益条款$", line)
    )


def _looks_like_new_section(line: str) -> bool:
    return bool(
        re.match(r"^第[零一二三四五六七八九十百千万\d]+[条款章节]", line)
        or re.match(r"^(chapter|section|article)\s+\d+", line, re.IGNORECASE)
    )


def _ends_with_list_item(text: str) -> bool:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return bool(_LIST_START.match(stripped))
    return False


def _numbered_list_continues(curr_text: str, nxt_text: str) -> bool:
    curr_match = None
    for line in reversed(curr_text.splitlines()):
        curr_match = _NUMBERED_LIST_START.match(line.strip())
        if curr_match:
            break
    next_match = _NUMBERED_LIST_START.match(nxt_text)
    if not curr_match or not next_match:
        return False
    return int(next_match.group(1)) == int(curr_match.group(1)) + 1


def _looks_like_page_continuation(curr: ChildChunk, nxt: ChildChunk) -> bool:
    if nxt.page_span[0] != curr.page_span[1] + 1:
        return False
    curr_bbox = curr.bbox_refs[-1].bbox if curr.bbox_refs else None
    next_bbox = nxt.bbox_refs[0].bbox if nxt.bbox_refs else None
    if curr_bbox is None or next_bbox is None:
        return False
    return curr_bbox.y1 >= 700 and next_bbox.y0 <= 140


def _absorb(
    parent: ParentChunk | None,
    target: ChildChunk,
    source: ChildChunk,
    *,
    drop_trailing_hyphen: bool = False,
) -> None:
    """Merge source into target; remove source from parent.child_chunk_ids."""
    target_text = target.text.rstrip()
    if drop_trailing_hyphen and target_text.endswith("-"):
        target_text = target_text[:-1].rstrip()
    last_ch = target_text[-1:] if target_text else ''
    sep = "" if drop_trailing_hyphen else (" " if last_ch.isascii() and last_ch not in '.!?' else "")
    target.text = target_text + sep + source.text.lstrip()
    target.page_span = (
        min(target.page_span[0], source.page_span[0]),
        max(target.page_span[1], source.page_span[1]),
    )
    target.source_block_ids = list(dict.fromkeys(target.source_block_ids + source.source_block_ids))
    target.bbox_refs = target.bbox_refs + source.bbox_refs
    target.token_count = estimate_tokens(target.text)
    if parent is not None:
        parent.page_span = (
            min(parent.page_span[0], source.page_span[0]),
            max(parent.page_span[1], source.page_span[1]),
        )
        parent.source_block_ids = list(dict.fromkeys(parent.source_block_ids + source.source_block_ids))
        try:
            parent.child_chunk_ids.remove(source.chunk_id)
        except ValueError:
            pass
