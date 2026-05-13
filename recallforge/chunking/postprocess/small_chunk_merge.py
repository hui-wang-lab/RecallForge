"""Post-processing pass to merge orphan small child chunks with adjacent siblings."""
from __future__ import annotations

from recallforge.chunking.ir.models import ChildChunk, ChunkPackage, ParentChunk

PROTECTED_TYPES = {"table", "figure", "caption", "image_context"}
_DEFAULT_MIN_TOKENS = 30


def merge_small_chunks(package: ChunkPackage, min_tokens: int | None = None) -> list[str]:
    """Merge child chunks below *min_tokens* into an adjacent sibling.

    Priority: merge with the next sibling; fall back to the previous.
    Protected chunk types (table, figure, caption) are never merged or used as merge targets.
    Mutates package.child_chunks and parent.child_chunk_ids in-place.
    Returns informational messages about each merge.
    """
    threshold = min_tokens if min_tokens is not None else _threshold(package)
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
        changed = True
        while changed:
            changed = False
            for i, child in enumerate(children):
                if child.chunk_type in PROTECTED_TYPES:
                    continue
                if child.token_count >= threshold:
                    continue

                # Prefer merging forward (absorb next into self)
                if i + 1 < len(children):
                    nxt = children[i + 1]
                    if nxt.chunk_type not in PROTECTED_TYPES:
                        saved_tok = nxt.token_count
                        _absorb(parent, child, nxt)
                        children.pop(i + 1)
                        to_remove.add(nxt.chunk_id)
                        warnings.append(
                            f"[small_chunk_merge] {nxt.chunk_id} ({saved_tok} tok) → {child.chunk_id}"
                        )
                        changed = True
                        break

                # Fall back: merge self into previous
                if i > 0:
                    prev = children[i - 1]
                    if prev.chunk_type not in PROTECTED_TYPES:
                        saved_tok = child.token_count
                        _absorb(parent, prev, child)
                        children.pop(i)
                        to_remove.add(child.chunk_id)
                        warnings.append(
                            f"[small_chunk_merge] {child.chunk_id} ({saved_tok} tok) → {prev.chunk_id}"
                        )
                        changed = True
                        break

    if to_remove:
        package.child_chunks = [c for c in package.child_chunks if c.chunk_id not in to_remove]

    return warnings


def _threshold(package: ChunkPackage) -> int:
    value = (package.metadata.get("chunker_config") or {}).get("child_min_tokens")
    try:
        configured = int(value)
        # merge if below half the configured minimum
        return max(1, configured // 2)
    except (TypeError, ValueError):
        return _DEFAULT_MIN_TOKENS


def _absorb(parent: ParentChunk, target: ChildChunk, source: ChildChunk) -> None:
    """Append source text to target; remove source from parent.child_chunk_ids."""
    target_end = target.text.rstrip()
    last_ch = target_end[-1:] if target_end else ''
    sep = "\n\n" if last_ch in '.。!！?？…' else " "
    target.text = target_end + sep + source.text.lstrip()
    target.page_span = (
        min(target.page_span[0], source.page_span[0]),
        max(target.page_span[1], source.page_span[1]),
    )
    target.source_block_ids = list(dict.fromkeys(target.source_block_ids + source.source_block_ids))
    target.bbox_refs = target.bbox_refs + source.bbox_refs
    target.token_count = target.token_count + source.token_count
    try:
        parent.child_chunk_ids.remove(source.chunk_id)
    except ValueError:
        pass
