"""Validation helpers for parsed documents and chunk packages."""
from __future__ import annotations

from recallforge.chunking.ir.models import ChunkPackage, ParsedDocument
from recallforge.chunking.ir.normalize import bbox_in_page

_HEADING_TYPES = {"heading", "title", "section_heading"}


def validate_parsed_document(document: ParsedDocument) -> list[str]:
    warnings: list[str] = []
    if not document.pages:
        warnings.append("ParsedDocument has no pages.")
    if not document.blocks:
        warnings.append("ParsedDocument has no blocks.")

    seen_blocks: set[str] = set()
    pages_by_number = {page.page_number: page for page in document.pages}

    for block in document.blocks:
        if block.block_id in seen_blocks:
            warnings.append(f"Duplicate block_id: {block.block_id}")
        seen_blocks.add(block.block_id)
        if block.page_number <= 0:
            warnings.append(f"Block {block.block_id} has invalid page_number.")
        page = pages_by_number.get(block.page_number)
        if page is None:
            warnings.append(f"Block {block.block_id} references missing page {block.page_number}.")
        elif block.bbox is not None and not bbox_in_page(block.bbox, page.width, page.height):
            warnings.append(f"Block {block.block_id} bbox is outside page bounds.")
        if block.bbox is not None:
            if block.bbox.x0 == block.bbox.x1 or block.bbox.y0 == block.bbox.y1:
                warnings.append(f"Block {block.block_id} has zero-area bbox.")
        if block.block_id not in _page_block_ids(document, block.page_number):
            warnings.append(f"Block {block.block_id} is missing from page block_ids.")

    warnings.extend(_check_reading_order(document))
    warnings.extend(_check_heading_path_sources(document))
    warnings.extend(_check_section_tree_integrity(document))

    return warnings


def _check_reading_order(document: ParsedDocument) -> list[str]:
    """reading_order must be non-decreasing within each page."""
    warnings: list[str] = []
    page_blocks: dict[int, list[int]] = {}
    for block in document.blocks:
        page_blocks.setdefault(block.page_number, []).append(block.reading_order)

    for page_number, orders in page_blocks.items():
        for j in range(1, len(orders)):
            if orders[j] < orders[j - 1]:
                warnings.append(
                    f"Page {page_number}: reading_order not monotonic "
                    f"({orders[j - 1]} → {orders[j]})."
                )
                break  # one warning per page is enough
    return warnings


def _check_heading_path_sources(document: ParsedDocument) -> list[str]:
    """heading_path should match the section tree and not appear out of nowhere.

    A block's heading_path must match an entry in document.section_tree.
    """
    warnings: list[str] = []
    if not document.section_tree:
        return warnings

    valid_paths: set[tuple[str, ...]] = {
        tuple(section.heading_path) for section in document.section_tree
    }

    for block in document.blocks:
        if not block.heading_path:
            continue
        path_key = tuple(block.heading_path)
        if path_key not in valid_paths:
            warnings.append(
                f"Block {block.block_id} has heading_path {block.heading_path!r} "
                f"that does not match any section in section_tree."
            )
    return warnings


def _check_section_tree_integrity(document: ParsedDocument) -> list[str]:
    """Section tree parent references must be consistent."""
    warnings: list[str] = []
    section_ids = {s.section_id for s in document.section_tree}
    for section in document.section_tree:
        if not section.title.strip():
            warnings.append(f"Section {section.section_id} has empty title.")
        if section.page_start > section.page_end:
            warnings.append(
                f"Section {section.section_id} has page_start > page_end "
                f"({section.page_start} > {section.page_end})."
            )
        if section.parent_section_id is not None and section.parent_section_id not in section_ids:
            warnings.append(
                f"Section {section.section_id} references missing parent {section.parent_section_id}."
            )
    return warnings


def _page_block_ids(document: ParsedDocument, page_number: int) -> set[str]:
    for page in document.pages:
        if page.page_number == page_number:
            return set(page.block_ids)
    return set()


def validate_chunk_package(package: ChunkPackage) -> list[str]:
    warnings: list[str] = []
    parent_map = {parent.parent_id: parent for parent in package.parent_chunks}
    child_ids: set[str] = set()
    block_to_child_types: dict[str, set[str]] = {}

    for child in package.child_chunks:
        if child.chunk_id in child_ids:
            warnings.append(f"Duplicate chunk_id: {child.chunk_id}")
        child_ids.add(child.chunk_id)

        if child.parent_id not in parent_map:
            warnings.append(f"Child {child.chunk_id} references missing parent.")
        else:
            # Bidirectional integrity: parent must know about this child
            parent = parent_map[child.parent_id]
            if child.chunk_id not in parent.child_chunk_ids:
                warnings.append(
                    f"Child {child.chunk_id} not listed in parent {child.parent_id}.child_chunk_ids."
                )

        if not child.source_block_ids:
            warnings.append(f"Child {child.chunk_id} has no source_block_ids.")
        if child.page_span[0] <= 0 or child.page_span[1] < child.page_span[0]:
            warnings.append(f"Child {child.chunk_id} has invalid page_span.")

        for block_id in child.source_block_ids:
            block_to_child_types.setdefault(block_id, set()).add(child.chunk_type)

    # Forward integrity: every ID in parent.child_chunk_ids must exist
    for parent in package.parent_chunks:
        for child_id in parent.child_chunk_ids:
            if child_id not in child_ids:
                warnings.append(
                    f"Parent {parent.parent_id} references missing child {child_id}."
                )

    # Table/figure block protection: media blocks must not be swallowed by text chunks
    _MEDIA_BLOCK_TYPES = {"table", "figure"}
    _MEDIA_CHUNK_TYPES = {"table", "figure", "caption", "image_context"}
    block_type_map = {block.block_id: block.block_type for block in package.blocks}
    for block_id, block_type in block_type_map.items():
        if block_type not in _MEDIA_BLOCK_TYPES:
            continue
        child_types = block_to_child_types.get(block_id)
        if child_types and not child_types.issubset(_MEDIA_CHUNK_TYPES):
            non_media = child_types - _MEDIA_CHUNK_TYPES
            warnings.append(
                f"Block {block_id} (type={block_type}) is referenced by "
                f"non-media child chunk types: {non_media}."
            )

    return warnings
