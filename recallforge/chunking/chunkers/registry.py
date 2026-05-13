"""Template chunker registry."""
from __future__ import annotations

from recallforge.chunking.chunkers.base import TemplateChunker
from recallforge.chunking.chunkers.book import BookChunker
from recallforge.chunking.chunkers.contract_terms import ContractTermsChunker
from recallforge.chunking.chunkers.generic_structured import GenericStructuredChunker
from recallforge.chunking.chunkers.laws import LawsChunker
from recallforge.chunking.chunkers.manual import ManualChunker
from recallforge.chunking.chunkers.paper import PaperChunker
from recallforge.chunking.chunkers.picture_pdf import PicturePdfChunker
from recallforge.chunking.chunkers.qa import QAChunker
from recallforge.chunking.chunkers.table_data import TableDataChunker


def get_chunker(document_type: str) -> TemplateChunker:
    if document_type == "contract_terms":
        return ContractTermsChunker()
    if document_type == "book":
        return BookChunker()
    if document_type == "laws":
        return LawsChunker()
    if document_type == "paper":
        return PaperChunker()
    if document_type == "manual":
        return ManualChunker()
    if document_type == "table_data":
        return TableDataChunker()
    if document_type == "picture_pdf":
        return PicturePdfChunker()
    if document_type == "qa":
        return QAChunker()
    return GenericStructuredChunker()


def available_templates() -> list[str]:
    return [
        "contract_terms",
        "paper",
        "book",
        "manual",
        "laws",
        "table_data",
        "picture_pdf",
        "qa",
        "generic_structured",
    ]
