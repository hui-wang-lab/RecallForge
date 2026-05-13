"""Lightweight document type detection."""
from __future__ import annotations

from dataclasses import dataclass, field

from recallforge.chunking.ir.models import ParsedDocument

SUPPORTED_TEMPLATES = (
    "contract_terms",
    "paper",
    "book",
    "manual",
    "laws",
    "table_data",
    "picture_pdf",
    "qa",
    "generic_structured",
)


@dataclass(frozen=True)
class DocumentTypeDetection:
    document_type: str
    confidence: float
    signals: list[str] = field(default_factory=list)
    requested: str = "auto"


def detect_document_type(document: ParsedDocument, requested: str = "auto") -> str:
    return detect_document_type_details(document, requested=requested).document_type


def detect_document_type_details(document: ParsedDocument, requested: str = "auto") -> DocumentTypeDetection:
    if requested != "auto":
        document_type = requested if requested in SUPPORTED_TEMPLATES else "generic_structured"
        return DocumentTypeDetection(
            document_type=document_type,
            confidence=1.0 if requested in SUPPORTED_TEMPLATES else 0.2,
            signals=[f"requested:{requested}"],
            requested=requested,
        )
    if document.document_type in SUPPORTED_TEMPLATES:
        return DocumentTypeDetection(
            document_type=str(document.document_type),
            confidence=0.95,
            signals=[f"parser_document_type:{document.document_type}"],
            requested=requested,
        )

    sample = _sample_text(document).lower()
    table_ratio = _ratio(document, "table")
    figure_ratio = _ratio(document, "figure")

    if table_ratio > 0.35:
        return DocumentTypeDetection(
            "table_data",
            min(0.95, table_ratio),
            [f"table_ratio:{table_ratio:.2f}"],
            requested,
        )
    if figure_ratio > 0.35:
        return DocumentTypeDetection(
            "picture_pdf",
            min(0.95, figure_ratio),
            [f"figure_ratio:{figure_ratio:.2f}"],
            requested,
        )

    for document_type, needles in _keyword_rules():
        matches = _matches(sample, needles)
        if matches:
            confidence = min(0.9, 0.55 + 0.1 * len(matches))
            return DocumentTypeDetection(document_type, confidence, matches, requested)

    return DocumentTypeDetection("generic_structured", 0.3, ["fallback"], requested)


def _sample_text(document: ParsedDocument, limit: int = 12000) -> str:
    parts: list[str] = []
    current = 0
    for block in document.blocks:
        if not block.text:
            continue
        parts.append(block.text)
        current += len(block.text)
        if current >= limit:
            break
    return "\n".join(parts)


def _ratio(document: ParsedDocument, block_type: str) -> float:
    if not document.blocks:
        return 0.0
    return sum(1 for block in document.blocks if block.block_type == block_type) / len(document.blocks)


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _matches(text: str, needles: tuple[str, ...]) -> list[str]:
    return [needle for needle in needles if needle in text]


def _keyword_rules() -> tuple[tuple[str, tuple[str, ...]], ...]:
    return (
        (
            "contract_terms",
            (
                "\u4fdd\u9669",
                "\u5408\u540c",
                "\u8d23\u4efb\u514d\u9664",
                "\u91ca\u4e49",
                "\u6761\u6b3e",
                "policy",
                "insured",
                "premium",
                "exclusion",
            ),
        ),
        ("paper", ("abstract", "references", "introduction", "methodology", "doi", "keywords")),
        ("book", ("chapter ", "contents", "table of contents", "preface", "appendix", "\u76ee\u5f55")),
        (
            "laws",
            (
                "\u4e2d\u534e\u4eba\u6c11\u5171\u548c\u56fd",
                "\u7b2c\u4e00\u6761",
                "\u7b2c\u4e8c\u6761",
                "\u6761\u4f8b",
                "\u529e\u6cd5",
                "regulation",
                "article 1",
                "article 2",
            ),
        ),
        ("manual", ("troubleshooting", "maintenance", "installation", "warning", "caution", "procedure")),
        ("qa", ("question:", "answer:", "q:", "a:", "faq", "\u95ee\uff1a", "\u7b54\uff1a")),
    )
