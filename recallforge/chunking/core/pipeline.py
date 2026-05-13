"""Phase 1 document understanding and chunking pipeline."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from recallforge.chunking.chunkers.base import ChunkerConfig
from recallforge.chunking.chunkers.registry import get_chunker
from recallforge.chunking.core.debug import build_debug_payload
from recallforge.chunking.core.document_type import detect_document_type_details
from recallforge.chunking.ir.layout_noise import clean_layout_noise
from recallforge.chunking.ir.models import ChunkPackage, ParsedDocument
from recallforge.chunking.ir.section_tree import build_section_tree
from recallforge.chunking.ir.validators import validate_chunk_package, validate_parsed_document
from recallforge.chunking.parsers.base import ParserAdapter, ParserConfig
from recallforge.chunking.parsers.docling_pdf import DoclingPdfParser
from recallforge.chunking.parsers.mineru_pdf import MinerUPdfParser
from recallforge.chunking.parsers.pypdf_fallback import PyPdfFallbackParser
from recallforge.chunking.parsers.table_file import TableFileParser
from recallforge.chunking.parsers.text_file import TextFileParser
from recallforge.chunking.postprocess.boundary_repair import repair_boundaries
from recallforge.chunking.postprocess.media_context import attach_media_context
from recallforge.chunking.postprocess.overlong_split import split_overlong_chunks
from recallforge.chunking.postprocess.quality import add_quality_metrics
from recallforge.chunking.postprocess.small_chunk_merge import merge_small_chunks

logger = logging.getLogger("recallforge.chunking.pipeline")

DEFAULT_PARSER_PRIORITY = ("docling", "mineru", "pypdf")


@dataclass(frozen=True)
class PipelineConfig:
    parser: str = "auto"
    template: str = "auto"
    max_tokens: int = 400
    chunk_size_tokens: int = 400
    overlap_tokens: int = 100
    min_chunk_tokens: int = 50
    child_max_tokens: int = 450
    child_min_tokens: int = 80
    parent_granularity: str = "chapter"
    table_context_blocks: int = 2
    image_context_blocks: int = 2
    include_blocks: bool = True
    include_debug: bool = False


def parse_to_chunk_package(file_path: str | os.PathLike[str], config: PipelineConfig) -> ChunkPackage:
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    parser_config = ParserConfig(
        max_tokens=config.max_tokens,
        chunk_size_tokens=config.chunk_size_tokens,
        overlap_tokens=config.overlap_tokens,
        min_chunk_tokens=config.min_chunk_tokens,
    )

    document, parser_warnings = _parse_with_fallback(path, config.parser, parser_config)
    parser_warnings.extend(clean_layout_noise(document))
    document = build_section_tree(document)

    doc_warnings = validate_parsed_document(document)
    type_detection = detect_document_type_details(document, requested=config.template)
    document_type = type_detection.document_type
    document.document_type = document_type

    chunker = get_chunker(document_type)
    chunker_config = ChunkerConfig(
        child_max_tokens=config.child_max_tokens,
        child_min_tokens=config.child_min_tokens,
        parent_granularity=config.parent_granularity,
        table_context_blocks=config.table_context_blocks,
        image_context_blocks=config.image_context_blocks,
    )
    result = chunker.chunk(document, chunker_config)
    attach_media_context(result.child_chunks, document, chunker_config)

    document.parse_report.parser_used = document.parser_used
    document.parse_report.parser_fallback_chain = list(document.parser_fallback_chain)

    package = ChunkPackage(
        document_id=document.document_id,
        document_type=document_type,
        parser_used=document.parser_used,
        chunker_used=result.chunker_used,
        parent_chunks=result.parent_chunks,
        child_chunks=result.child_chunks,
        blocks=document.blocks,
        parse_report=document.parse_report,
        warnings=[*parser_warnings, *doc_warnings, *result.warnings],
        metadata={
            "filename": document.filename,
            "file_type": document.file_type,
            "section_count": len(document.section_tree),
            "layout_noise_removed_count": document.metadata.get("layout_noise_removed_count", 0),
            "layout_noise_removed_examples": list(document.metadata.get("layout_noise_removed_examples", [])),
            "document_type_detection": {
                "confidence": type_detection.confidence,
                "signals": list(type_detection.signals),
                "requested": type_detection.requested,
            },
            "chunker_config": {
                "child_max_tokens": config.child_max_tokens,
                "child_min_tokens": config.child_min_tokens,
                "parent_granularity": config.parent_granularity,
                "table_context_blocks": config.table_context_blocks,
                "image_context_blocks": config.image_context_blocks,
            },
        },
    )
    package.warnings.extend(repair_boundaries(package))
    package.warnings.extend(merge_small_chunks(package))
    package.warnings.extend(split_overlong_chunks(package))
    package.warnings.extend(validate_chunk_package(package))
    package.parse_report.warnings = list(dict.fromkeys([*package.parse_report.warnings, *package.warnings]))
    package.parse_report.parent_chunk_count = len(package.parent_chunks)
    package.parse_report.child_chunk_count = len(package.child_chunks)
    add_quality_metrics(package)
    if config.include_debug:
        package.debug = build_debug_payload(document, package)
    return package


def configured_parser_priority(parser: str = "auto") -> list[str]:
    if parser == "docling":
        return ["docling", "pypdf"]
    if parser == "mineru":
        return ["mineru", "pypdf"]
    if parser == "pypdf":
        return ["pypdf"]
    if parser == "table_file":
        return ["table_file"]
    if parser == "text_file":
        return ["text_file"]

    raw = os.getenv("CHUNKFLOW_PARSER_PRIORITY", ",".join(DEFAULT_PARSER_PRIORITY))
    priority = [part.strip().lower() for part in raw.split(",") if part.strip()]
    allowed = [name for name in priority if name in DEFAULT_PARSER_PRIORITY]
    return allowed or list(DEFAULT_PARSER_PRIORITY)


def available_parsers() -> dict[str, bool]:
    adapters = _parser_adapters()
    return {name: adapter.is_available() for name, adapter in adapters.items()}


def _parse_with_fallback(
    path: Path,
    parser: str,
    parser_config: ParserConfig,
) -> tuple[ParsedDocument, list[str]]:
    adapters = _parser_adapters()
    priority = _priority_for_path(path, parser)
    attempted: list[str] = []
    warnings: list[str] = []

    for parser_name in priority:
        adapter = adapters[parser_name]
        attempted.append(parser_name)
        if not adapter.is_available():
            warnings.append(f"Parser {parser_name} is not available.")
            continue
        try:
            logger.info("Parsing %s with %s", path, parser_name)
            document = adapter.parse(path, parser_config)
            if not document.blocks:
                warnings.append(f"Parser {parser_name} produced no blocks.")
                continue
            document.parser_fallback_chain = list(attempted)
            document.parse_report.warnings.extend(warnings)
            return document, warnings
        except Exception as exc:
            logger.warning("%s parser failed: %s", parser_name, exc)
            warnings.append(f"Parser {parser_name} failed: {exc}")

    raise RuntimeError(f"No parser could parse {path}. Attempts: {', '.join(attempted)}")


def _parser_adapters() -> dict[str, ParserAdapter]:
    return {
        "docling": DoclingPdfParser(),
        "mineru": MinerUPdfParser(),
        "pypdf": PyPdfFallbackParser(),
        "table_file": TableFileParser(),
        "text_file": TextFileParser(),
    }


def _priority_for_path(path: Path, parser: str) -> list[str]:
    suffix = path.suffix.lower()
    if parser != "auto":
        return configured_parser_priority(parser)
    if suffix in {".csv", ".tsv", ".xlsx", ".xlsm"}:
        return ["table_file"]
    if suffix in {".txt", ".md", ".markdown"}:
        return ["text_file"]
    return configured_parser_priority(parser)
