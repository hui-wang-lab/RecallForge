"""Question-answer document chunker."""
from __future__ import annotations

import re
from collections import OrderedDict

from recallforge.chunking.chunkers.base import ChunkerConfig, ChunkingResult, TemplateChunker
from recallforge.chunking.chunkers.template_utils import (
    BlockGroup,
    make_child_from_blocks,
    make_parent_from_blocks,
    ordered_content_blocks,
)
from recallforge.chunking.ir.models import Block, ParsedDocument

_Q_RE = re.compile(r"^\s*(?:q(?:uestion)?\s*[:：]|问[:：]|问题[:：])", re.IGNORECASE)
_A_RE = re.compile(r"^\s*(?:a(?:nswer)?\s*[:：]|答[:：]|答案[:：])", re.IGNORECASE)


class QAChunker(TemplateChunker):
    name = "qa"

    def chunk(self, document: ParsedDocument, config: ChunkerConfig) -> ChunkingResult:
        groups = _category_groups(ordered_content_blocks(document))
        parents = []
        children = []

        for group in groups.values():
            parent = make_parent_from_blocks(document, group)
            local = _qa_children(document, parent, group.blocks, self.name)
            parent.child_chunk_ids = [child.chunk_id for child in local]
            parents.append(parent)
            children.extend(local)

        return ChunkingResult(chunker_used=self.name, parent_chunks=parents, child_chunks=children)


def _category_groups(blocks: list[Block]) -> "OrderedDict[str, BlockGroup]":
    groups: "OrderedDict[str, BlockGroup]" = OrderedDict()
    current = BlockGroup(
        key="QA",
        title="QA",
        heading_path=["QA"],
        metadata={"template_rule": "qa_category"},
    )

    for block in blocks:
        if block.block_type == "heading" and not _Q_RE.match(_first_line(block)):
            first = _first_line(block)
            current = BlockGroup(
                key=first,
                title=first,
                heading_path=[first],
                metadata={"template_rule": "qa_category"},
            )
        elif current.key == "QA" and block.heading_path:
            current.title = block.heading_path[0]
            current.heading_path = [block.heading_path[0]]
            current.key = block.heading_path[0]
        groups.setdefault(current.key, current).blocks.append(block)
    return groups


def _qa_children(document: ParsedDocument, parent, blocks: list[Block], template: str):
    children = []
    current: list[Block] = []
    question: str | None = None
    answer_parts: list[str] = []

    def flush() -> None:
        nonlocal current, question, answer_parts
        if current:
            children.append(
                make_child_from_blocks(
                    document,
                    template,
                    parent,
                    len(children),
                    current,
                    chunk_type="qa_pair",
                    heading_path=parent.heading_path,
                    metadata={
                        "question": question,
                        "answer": "\n\n".join(answer_parts).strip() or None,
                        "template_rule": "qa_pair",
                    },
                )
            )
        current = []
        question = None
        answer_parts = []

    for block in blocks:
        first = _first_line(block)
        if block.block_type == "heading" and not _Q_RE.match(first):
            continue
        if _Q_RE.match(first) and current:
            flush()
        if _Q_RE.match(first):
            question = _strip_marker(first, _Q_RE)
        elif _A_RE.match(first):
            answer_parts.append(_strip_marker(block.text, _A_RE))
        elif question and not answer_parts:
            answer_parts.append(block.text)
        elif current:
            answer_parts.append(block.text)
        current.append(block)

    flush()
    return children


def _first_line(block: Block) -> str:
    return next((line.strip() for line in block.text.splitlines() if line.strip()), "")


def _strip_marker(text: str, pattern: re.Pattern[str]) -> str:
    return pattern.sub("", text, count=1).strip()
