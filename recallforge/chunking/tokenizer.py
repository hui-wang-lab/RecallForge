"""Shared whitespace tokenizer for chunking."""
from __future__ import annotations

import re

_CJK_RANGES = (
    "\u4e00-\u9fff"    # CJK Unified Ideographs
    "\u3400-\u4dbf"    # CJK Extension A
    "\uf900-\ufaff"    # CJK Compatibility Ideographs
    "\U00020000-\U0002a6df"  # CJK Extension B
)
_CJK_RE = re.compile(f"[{_CJK_RANGES}]")
_LATIN_WORD_RE = re.compile(r"[a-zA-Z0-9]+(?:[-'][a-zA-Z0-9]+)*")


def simple_tokenize(text: str) -> list[str]:
    return text.split()


def estimate_tokens(text: str) -> int:
    """Estimate token count for mixed CJK/Latin text.

    CJK characters are counted individually (~1.5 subword tokens each in BERT,
    but 1:1 is a safe lower-bound for merge decisions).
    Latin words are counted as ~1.3 tokens each (sub-word expansion).
    """
    cjk_count = len(_CJK_RE.findall(text))
    latin_words = len(_LATIN_WORD_RE.findall(text))
    return cjk_count + int(latin_words * 1.3)
