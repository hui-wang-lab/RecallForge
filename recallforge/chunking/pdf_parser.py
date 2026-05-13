"""Fallback PDF parser: pypdf-based text extraction with per-page metadata."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

_CN_NUM = r"[一二三四五六七八九十百千万零\d]+"

CHAPTER_PATTERN = re.compile(
    r"(?:Chapter\s+(\d+(?:-\d+)?)"
    r"|第(" + _CN_NUM + r")[章编部篇])",
    re.IGNORECASE,
)

SECTION_PATTERN = re.compile(
    r"(?:"
    r"Section\s+(\d+)\.\s*(.+?)(?=\n|Section\s+\d+|Chapter\s+\d+|$)"
    r"|第(" + _CN_NUM + r")[条节]\s*(.{0,40})"
    r")",
    re.IGNORECASE | re.DOTALL,
)

DOMAIN_HINT_KEYWORDS: dict[str, list[str]] = {
    "Engine": ["engine", "starter", "oil", "coolant", "temperature", "fuel", "rpm", "crankcase"],
    "Generator": ["generator", "voltage", "regulator", "contactor", "rectifier", "field", "brush"],
    "Troubleshooting": ["troubleshoot", "fault", "symptom", "will not", "won't", "problem", "fails", "stops"],
    "保险条款": ["保险金", "被保险人", "投保人", "保险费", "保险期间", "保险责任", "责任免除", "犹豫期"],
    "理赔": ["理赔", "给付", "赔偿", "赔付", "申请保险金"],
    "合同条款": ["合同成立", "合同效力", "合同解除", "合同终止", "合同变更", "现金价值"],
}


@dataclass
class PageMetadata:
    chapter: Optional[str] = None
    section: Optional[str] = None
    section_title: Optional[str] = None
    domain_hint: Optional[str] = None


def extract_metadata_from_page_text(page_number: int, text: str) -> Optional[PageMetadata]:
    if not text or not text.strip():
        return None
    try:
        chapter: Optional[str] = None
        section: Optional[str] = None
        section_title: Optional[str] = None

        ch_match = CHAPTER_PATTERN.search(text)
        if ch_match:
            chapter = (ch_match.group(1) or ch_match.group(2) or "").strip()
            if ch_match.group(2):
                chapter = ch_match.group(0).strip()

        sec_match = SECTION_PATTERN.search(text)
        if sec_match:
            if sec_match.group(1):
                section = sec_match.group(1).strip()
                raw_title = (sec_match.group(2) or "").strip()
            else:
                section = sec_match.group(0).strip()
                raw_title = (sec_match.group(4) or "").strip()
            section_title = raw_title.split("\n")[0][:80].strip() if raw_title else None

        domain_hint = _infer_domain_hint_from_text(text)
        return PageMetadata(
            chapter=chapter or None,
            section=section or None,
            section_title=section_title or None,
            domain_hint=domain_hint,
        )
    except Exception:
        return None


def _infer_domain_hint_from_text(text: str) -> Optional[str]:
    if not text or not text.strip():
        return None
    low = text.lower()
    matched: list[str] = []
    for domain, keywords in DOMAIN_HINT_KEYWORDS.items():
        for kw in keywords:
            if kw in low:
                matched.append(domain)
                break
    if len(matched) == 1:
        return matched[0]
    if len(matched) > 1:
        if "Troubleshooting" in matched:
            return "Troubleshooting"
        return matched[0]
    return None


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------

_PAGE_MARKER_RE = re.compile(
    r"(?:"
    r"第\s*\d+\s*页(?:\s*(?:共|/)\s*\d+\s*页)?"
    r"|(?:^|\s)[-—]\s*\d+\s*[-—](?:\s|$)"
    r"|Page\s+\d+(?:\s+of\s+\d+)?"
    r")",
    re.IGNORECASE,
)


def clean_chunk_text(text: str) -> str:
    """Remove page markers (e.g. '第 1 页', '- 3 -', 'Page 5') from chunk text."""
    cleaned = _PAGE_MARKER_RE.sub("", text)
    cleaned_lines: list[str] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        # Removing "第 N 页" can leave a naked OCR number like "20251".
        if re.fullmatch(r"\d{4,8}", stripped):
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


_SENTENCE_END_RE = re.compile(r"[。！？；.!?;）)」』\"]$")


def clean_page_texts(
    page_texts: list[tuple[int, str]],
    *,
    min_pages_for_detection: int = 3,
    frequency_threshold: float = 0.5,
    max_header_footer_lines: int = 3,
) -> list[tuple[int, str]]:
    """Strip repeated header/footer lines that appear on many pages.

    A line (after stripping) is considered a header/footer candidate if it
    appears on at least *frequency_threshold* fraction of all pages, in the
    first or last *max_header_footer_lines* lines of a page.
    """
    if len(page_texts) < min_pages_for_detection:
        return page_texts

    header_counter: Counter[str] = Counter()
    footer_counter: Counter[str] = Counter()

    for _, text in page_texts:
        lines = text.splitlines()
        seen_h: set[str] = set()
        seen_f: set[str] = set()
        for line in lines[:max_header_footer_lines]:
            norm = _normalize_hf_line(line)
            if norm and norm not in seen_h:
                header_counter[norm] += 1
                seen_h.add(norm)
        for line in lines[-max_header_footer_lines:]:
            norm = _normalize_hf_line(line)
            if norm and norm not in seen_f:
                footer_counter[norm] += 1
                seen_f.add(norm)

    total = len(page_texts)
    min_freq = int(total * frequency_threshold)

    hf_patterns: set[str] = set()
    for line, cnt in header_counter.items():
        if cnt >= min_freq:
            hf_patterns.add(line)
    for line, cnt in footer_counter.items():
        if cnt >= min_freq:
            hf_patterns.add(line)

    if not hf_patterns:
        return page_texts

    cleaned: list[tuple[int, str]] = []
    for page_num, text in page_texts:
        lines = text.splitlines()
        out_lines: list[str] = []
        for i, line in enumerate(lines):
            norm = _normalize_hf_line(line)
            in_header_zone = i < max_header_footer_lines
            in_footer_zone = i >= len(lines) - max_header_footer_lines
            if norm in hf_patterns and (in_header_zone or in_footer_zone):
                continue
            out_lines.append(line)
        cleaned.append((page_num, "\n".join(out_lines).strip()))

    return cleaned


def _normalize_hf_line(line: str) -> str:
    """Normalize a line for header/footer comparison.

    Replaces page numbers with a placeholder so that lines like
    "XX保险条款 第1页" and "XX保险条款 第2页" are treated as the same pattern.
    """
    s = line.strip()
    if not s:
        return ""
    s = re.sub(r"\d+", "#", s)
    s = re.sub(r"\s+", " ", s)
    return s


def join_pages_smart(
    page_texts: list[tuple[int, str]],
) -> tuple[str, list[tuple[int, int, int]]]:
    """Join page texts intelligently and compute accurate character offsets.

    If a page does NOT end with sentence-ending punctuation, the paragraph
    likely continues on the next page, so we join without a blank line.

    Returns:
        (full_text, page_char_offsets) where *page_char_offsets* is a list of
        ``(page_number, start_char, end_char)`` tuples that map each page's
        text range within *full_text*.
    """
    if not page_texts:
        return "", []

    parts: list[str] = []
    page_char_offsets: list[tuple[int, int, int]] = []
    char_offset = 0

    for page_num, text in page_texts:
        if not text:
            page_char_offsets.append((page_num, char_offset, char_offset))
            continue

        if parts:
            prev_text = parts[-1]
            if _SENTENCE_END_RE.search(prev_text.rstrip()):
                sep = "\n\n"
            else:
                sep = ""
            parts.append(sep)
            char_offset += len(sep)

        start = char_offset
        parts.append(text)
        char_offset += len(text)
        page_char_offsets.append((page_num, start, char_offset))

    return "".join(parts), page_char_offsets
