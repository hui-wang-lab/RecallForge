"""Layout noise cleanup for parsed document blocks."""
from __future__ import annotations

import re
from collections import defaultdict

from recallforge.chunking.ir.models import Block, ParsedDocument

_PAGE_NUMBER_RE = re.compile(
    r"^(?:第\s*)?\d{1,4}\s*(?:页)?$"
    r"|^page\s+\d{1,4}$"
    r"|^[—–\-]\s*\d{1,4}\s*[—–\-]$",
    re.IGNORECASE,
)
_DOT_LEADER_RE = re.compile(r".*\.{4,}\s*\d{1,4}$")
_RUNNING_HEADER_HINT_RE = re.compile(r"(confidential|draft|copyright|版权所有|保密|内部资料)", re.IGNORECASE)
_INSURANCE_RUNNING_HEADER_RE = re.compile(
    r"(?:保险股份有限公司\s*)?个人保险基本条款第[零一二三四五六七八九十\d]+版$|"
    r"^[^。！？；：]{4,80}(?:寿险|保险|险)\S*利益条款$"
)
_LEGAL_ARTICLE_TITLE_RE = re.compile(r"^第[零一二三四五六七八九十百千万\d]+[条款章节]")
_NOISE_TYPES = {"header", "footer", "page_number"}


def clean_layout_noise(document: ParsedDocument) -> list[str]:
    """Remove common headers, footers, page numbers, and TOC leader rows.

    The pass is conservative: explicit parser noise types are removed directly;
    repeated short top/bottom blocks are removed only when their text appears on
    multiple pages. Removed block IDs are also deleted from page.block_ids.
    """
    if not document.blocks:
        return []

    repeated = _repeated_edge_texts(document.blocks)
    kept: list[Block] = []
    removed: list[Block] = []

    for block in document.blocks:
        if _is_noise_block(block, repeated):
            block.metadata["layout_noise_removed"] = True
            removed.append(block)
            continue
        kept.append(block)

    if not removed:
        document.metadata["layout_noise_removed_count"] = 0
        return []

    removed_ids = {block.block_id for block in removed}
    document.blocks = kept
    for page in document.pages:
        page.block_ids = [block_id for block_id in page.block_ids if block_id not in removed_ids]

    document.parse_report.block_count = len(document.blocks)
    document.parse_report.table_count = sum(1 for block in document.blocks if block.block_type == "table")
    document.parse_report.figure_count = sum(1 for block in document.blocks if block.block_type == "figure")
    document.metadata["layout_noise_removed_count"] = len(removed)
    document.metadata["layout_noise_removed_examples"] = [
        block.text.strip()[:80] for block in removed[:5] if block.text.strip()
    ]
    return [f"[layout_noise] removed {len(removed)} repeated/header/footer/page-number blocks"]


def _repeated_edge_texts(blocks: list[Block]) -> set[str]:
    pages_by_text: dict[str, set[int]] = defaultdict(set)
    for block in blocks:
        text = _normalized_text(block.text)
        if not text or len(text) > 120:
            continue
        if _is_page_edge(block) or _RUNNING_HEADER_HINT_RE.search(text) or _looks_like_running_header_text(block):
            pages_by_text[text].add(block.page_number)
    return {text for text, pages in pages_by_text.items() if len(pages) >= 2}


def _is_noise_block(block: Block, repeated_edge_texts: set[str]) -> bool:
    text = block.text.strip()
    normalized = _normalized_text(text)
    if not text:
        return True
    if block.block_type in _NOISE_TYPES:
        return True
    if _PAGE_NUMBER_RE.match(text) and len(text) <= 20:
        return True
    if _DOT_LEADER_RE.match(text) and len(text) <= 160:
        block.metadata["layout_noise_reason"] = "toc_dot_leader"
        return True
    if _looks_like_running_header_text(block):
        block.metadata["layout_noise_reason"] = "running_header_pattern"
        return True
    if normalized in repeated_edge_texts:
        block.metadata["layout_noise_reason"] = "repeated_page_edge"
        return True
    return False


def _is_page_edge(block: Block) -> bool:
    if block.bbox is None:
        return False
    y_mid = (block.bbox.y0 + block.bbox.y1) / 2
    return y_mid <= 90 or y_mid >= 700


def _looks_like_running_header_text(block: Block) -> bool:
    text = block.text.strip()
    if not text or block.page_number <= 1 or len(text) > 120:
        return False
    if _LEGAL_ARTICLE_TITLE_RE.match(text):
        return False
    if not _INSURANCE_RUNNING_HEADER_RE.search(text):
        return False
    if block.bbox is None:
        return True
    return _is_page_edge(block)


def _normalized_text(text: str) -> str:
    return " ".join(text.lower().split())
