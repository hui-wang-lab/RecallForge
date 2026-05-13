"""Docling-based PDF parser for structure-aware ingestion."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("recallforge.chunking.docling_parser")

_CN_CHAPTER_RE = re.compile(r"第[一二三四五六七八九十百千万零\d]+[章编部篇]")
_CN_SECTION_RE = re.compile(r"第[一二三四五六七八九十百千万零\d]+[条节]")

_DOCLING_AVAILABLE = False
try:
    from docling.chunking import HybridChunker
    from docling.document_converter import DocumentConverter
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
    from transformers import AutoTokenizer

    _DOCLING_AVAILABLE = True
except ImportError:
    pass


def is_docling_available() -> bool:
    return _DOCLING_AVAILABLE


def parse_pdf_with_docling(
    pdf_path: str | Path,
    tokenizer: str = "BAAI/bge-base-en-v1.5",
    max_tokens: int = 400,
) -> list[dict]:
    """
    Parse PDF using Docling. Returns list of chunk dicts with keys:
    raw_text, page_number, chapter, section, domain_hint, headings, content_type.
    """
    if not _DOCLING_AVAILABLE:
        raise ImportError("Docling is not installed. pip install docling")

    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    doc = result.document

    hf_tokenizer = HuggingFaceTokenizer(
        tokenizer=AutoTokenizer.from_pretrained(tokenizer),
        max_tokens=max_tokens,
    )
    chunker = HybridChunker(
        tokenizer=hf_tokenizer,
        merge_peers=True,
    )
    chunks = list(chunker.chunk(doc))

    parsed_chunks = []
    for chunk in chunks:
        text = chunker.contextualize(chunk)

        headings: list[str] = []
        page_number: Optional[int] = None
        content_type: Optional[str] = None

        if hasattr(chunk, "meta") and chunk.meta is not None:
            meta = chunk.meta
            if hasattr(meta, "headings") and meta.headings:
                headings = list(meta.headings)

            if hasattr(meta, "doc_items") and meta.doc_items:
                for item in meta.doc_items:
                    if hasattr(item, "label"):
                        label = getattr(item, "label", None)
                        if label is not None:
                            content_type = str(label) if not isinstance(label, str) else label
                    page_number = _extract_page_from_doc_item(item)
                    if page_number is not None:
                        break

            if page_number is None and hasattr(meta, "origin") and meta.origin is not None:
                page_number = _extract_page_from_origin(meta.origin)

        chapter: Optional[str] = None
        section: Optional[str] = None
        if headings:
            for h in headings:
                h_str = str(h) if not isinstance(h, str) else h
                h_lower = h_str.lower()
                if "chapter" in h_lower or _CN_CHAPTER_RE.search(h_str):
                    chapter = h_str
                elif section is None:
                    if _CN_SECTION_RE.search(h_str):
                        section = h_str
                    else:
                        section = h_str

        domain_hint = _infer_domain_from_headings(headings, text)

        parsed_chunks.append(
            {
                "raw_text": text,
                "page_number": page_number,
                "chapter": chapter,
                "section": section,
                "domain_hint": domain_hint,
                "headings": headings,
                "content_type": content_type,
            }
        )

    logger.info("Docling parsed %s: %d chunks", pdf_path, len(parsed_chunks))
    return parsed_chunks


def _extract_page_from_doc_item(item: object) -> Optional[int]:
    """Try to get a page number from a Docling doc_item.

    Docling returns 1-indexed page numbers; we pass them through as-is.
    """
    if hasattr(item, "prov") and item.prov:
        provs = item.prov if isinstance(item.prov, (list, tuple)) else [item.prov]
        for prov in provs:
            pn = getattr(prov, "page_no", None)
            if pn is None:
                pn = getattr(prov, "page", None)
            if pn is not None:
                return int(pn)

    if hasattr(item, "page_no"):
        pn = getattr(item, "page_no", None)
        if pn is not None:
            return int(pn)

    return None


def _extract_page_from_origin(origin: object) -> Optional[int]:
    """Fallback: try to get page number from chunk.meta.origin."""
    for attr in ("page_no", "page_number", "page"):
        pn = getattr(origin, attr, None)
        if pn is not None:
            return int(pn)
    return None


def _infer_domain_from_headings(headings: list[str], text: str) -> Optional[str]:
    headings_str = " ".join(str(h) for h in headings).lower()
    combined = headings_str + " " + text[:1200].lower()

    if "troubleshoot" in combined:
        return "Troubleshooting"
    if "removal" in combined or "installation" in combined:
        return "Engine"
    if "generator" in combined:
        return "Generator"
    if "maintenance" in combined or "lubrication" in combined or "filter" in combined:
        return "Maintenance"
    if "engine" in combined or "fuel" in combined or "coolant" in combined:
        return "Engine"
    if "electric" in combined or "circuit" in combined or "wiring" in combined:
        return "Electrical"
    if "repair" in combined or "torque" in combined or "adjustment" in combined:
        return "Maintenance"

    cn_combined = headings_str + " " + text[:1200]
    if any(kw in cn_combined for kw in ("责任免除", "免责")):
        return "责任免除"
    if any(kw in cn_combined for kw in ("保险责任", "保险金", "给付")):
        return "保险责任"
    if any(kw in cn_combined for kw in ("理赔", "赔偿", "赔付", "申请保险金")):
        return "理赔"
    if any(kw in cn_combined for kw in ("合同成立", "合同效力", "合同解除", "合同终止")):
        return "合同条款"
    if any(kw in cn_combined for kw in ("保险费", "缴费", "费率")):
        return "保险费"
    if any(kw in cn_combined for kw in ("犹豫期", "宽限期", "等待期")):
        return "期间条款"
    if any(kw in cn_combined for kw in ("被保险人", "投保人", "受益人")):
        return "保险条款"

    return None
