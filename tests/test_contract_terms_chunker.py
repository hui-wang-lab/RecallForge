"""Contract terms chunking quality tests."""

from __future__ import annotations

from recallforge.chunking.chunkers.base import ChunkerConfig
from recallforge.chunking.chunkers.contract_terms import ContractTermsChunker
from recallforge.chunking.ir.models import Block, ChunkPackage, ParsedDocument
from recallforge.ingest.chunk_adapter import IngestContext, build_chunks_for_ingest


def _document(blocks: list[Block]) -> ParsedDocument:
    return ParsedDocument(
        document_id="doc-contract",
        source_path="contract.pdf",
        filename="contract.pdf",
        file_type="pdf",
        document_type="contract_terms",
        parser_used="test",
        blocks=blocks,
    )


def _block(index: int, text: str, *, block_type: str = "paragraph") -> Block:
    return Block(
        block_id=f"b{index}",
        document_id="doc-contract",
        page_number=max(1, index // 4 + 1),
        block_type=block_type,
        text=text,
        reading_order=index,
    )


def test_contract_terms_falls_back_to_article_parents_without_chapters():
    blocks = [
        _block(0, "保险利益条款"),
        _block(1, "第一条 合同构成", block_type="heading"),
        _block(2, "本合同由保险条款、保险单和投保文件共同构成。"),
        _block(3, "第二条 投保范围", block_type="heading"),
        _block(4, "凡符合承保条件者均可作为被保险人。"),
        _block(5, "第三条 保险金额", block_type="heading"),
        _block(6, "保险金额由您和我们约定并在保险单上载明。"),
    ]

    result = ContractTermsChunker().chunk(_document(blocks), ChunkerConfig())

    assert len(result.parent_chunks) == 4
    assert result.parent_chunks[0].metadata["template_rule"] == "contract_parent_preamble"
    assert result.parent_chunks[1].title == "第一条 合同构成"
    assert result.parent_chunks[1].metadata["parent_fallback"] == "article"
    assert result.parent_chunks[2].title == "第二条 投保范围"
    assert result.parent_chunks[3].title == "第三条 保险金额"
    assert all(parent.metadata["token_count"] > 0 for parent in result.parent_chunks)
    assert any("[contract_parent_fallback]" in warning for warning in result.warnings)

    ingest_chunks = build_chunks_for_ingest(
        ChunkPackage(
            document_id="doc-contract",
            document_type="contract_terms",
            parser_used="test",
            chunker_used=result.chunker_used,
            parent_chunks=result.parent_chunks,
            child_chunks=result.child_chunks,
        ),
        IngestContext(
            tenant_id="tenant-a",
            user_id=None,
            source_uri="contract.pdf",
            source_name="contract.pdf",
            doc_type="pdf",
            department="sales",
            access_level="internal",
            document_version=1,
            embedding_provider="provider-x",
            embedding_model="model-y",
            embedding_dim=1024,
        ),
    )
    assert all(parent.token_count is not None for parent in ingest_chunks.parent_creates)


def test_contract_terms_detects_spaced_chinese_article_markers():
    blocks = [
        _block(0, "测试条款结构"),
        _block(1, "第 1 条 合同构成", block_type="heading"),
        _block(2, "本合同由保险条款、保险单和投保文件共同构成。"),
        _block(3, "第 2 条 投保范围", block_type="heading"),
        _block(4, "凡符合承保条件者均可作为被保险人。"),
        _block(5, "第 3 条 保险金额", block_type="heading"),
        _block(6, "保险金额由您和我们约定并在保险单上载明。"),
    ]

    result = ContractTermsChunker().chunk(_document(blocks), ChunkerConfig())

    assert len(result.parent_chunks) == 4
    assert result.parent_chunks[1].title == "第1条 合同构成"
    assert result.parent_chunks[1].metadata["parent_fallback"] == "article"
    assert result.parent_chunks[2].title == "第2条 投保范围"
    assert result.parent_chunks[3].title == "第3条 保险金额"
    assert any("[contract_parent_fallback]" in warning for warning in result.warnings)


def test_contract_terms_keeps_children_under_their_article_parent():
    blocks = [
        _block(0, "第一条 保险责任", block_type="heading"),
        _block(1, "我们按照合同约定承担保险责任。"),
        _block(2, "保险责任包括身故保险金和身体全残保险金。"),
        _block(3, "第二条 责任免除", block_type="heading"),
        _block(4, "因故意犯罪导致事故的，我们不承担给付责任。"),
        _block(5, "第三条 释义", block_type="heading"),
        _block(6, "保险费指您按照合同约定交纳的费用。"),
    ]

    result = ContractTermsChunker().chunk(_document(blocks), ChunkerConfig(child_max_tokens=28))

    first_article_parent = result.parent_chunks[0]
    first_article_children = [
        child for child in result.child_chunks if child.parent_id == first_article_parent.parent_id
    ]

    assert first_article_parent.title == "第一条 保险责任"
    assert len(first_article_children) >= 1
    assert {child.metadata["article"] for child in first_article_children} == {"第一条 保险责任"}
    assert all(child.heading_path == ["第一条 保险责任"] for child in first_article_children)


def test_contract_terms_keeps_explicit_chapter_grouping():
    blocks = [
        _block(0, "第一章 总则", block_type="heading"),
        _block(1, "第一条 合同构成", block_type="heading"),
        _block(2, "本合同由保险条款和保险单共同构成。"),
        _block(3, "第二条 投保范围", block_type="heading"),
        _block(4, "凡符合承保条件者均可作为被保险人。"),
        _block(5, "第二章 保险责任", block_type="heading"),
        _block(6, "第三条 保险金额", block_type="heading"),
        _block(7, "保险金额由您和我们约定。"),
    ]

    result = ContractTermsChunker().chunk(_document(blocks), ChunkerConfig())

    assert [parent.title for parent in result.parent_chunks] == ["第一章 总则", "第二章 保险责任"]
    assert all("parent_fallback" not in parent.metadata for parent in result.parent_chunks)
    assert not result.warnings
