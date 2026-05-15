"""Optional answer generation with strict citation validation."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from recallforge.config import Settings
from recallforge.retrieval.types import Reference

REFUSAL_ANSWER = "当前资料无法确认。"
_CITATION_RE = re.compile(r"\[(\d+)\]")


class AnswerGenerationError(RuntimeError):
    """Raised when a configured answer provider cannot produce a safe answer."""


@dataclass(frozen=True)
class AnswerGenerationRequest:
    question: str
    context_text: str
    references: list[Reference]


@dataclass(frozen=True)
class AnswerGenerationResult:
    answer: str
    metadata: dict[str, Any] = field(default_factory=dict)


class AnswerGenerator(Protocol):
    async def generate(self, request: AnswerGenerationRequest) -> AnswerGenerationResult: ...


class HTTPAnswerGenerator:
    """Generic HTTP LLM adapter driven entirely by configuration."""

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    async def generate(self, request: AnswerGenerationRequest) -> AnswerGenerationResult:
        if not self._settings.llm_provider or not self._settings.llm_model or not self._settings.llm_endpoint:
            raise AnswerGenerationError("answer generation provider, model, and endpoint must be configured")
        if not self._settings.llm_api_key and not self._settings.openai_api_key:
            raise AnswerGenerationError("answer generation API key is not configured")

        prompt = build_answer_prompt(request.question, request.context_text, request.references)
        answer, metadata = await self._call_provider(prompt)
        validation = validate_answer_citations(answer, request.references)
        if not validation.valid and self._settings.answer_repair_invalid_citations:
            answer, repair_metadata = await self._call_provider(build_repair_prompt(prompt, answer, validation.reason))
            metadata["repair"] = repair_metadata
            validation = validate_answer_citations(answer, request.references)
        if not validation.valid:
            return AnswerGenerationResult(
                answer=REFUSAL_ANSWER,
                metadata={
                    **metadata,
                    "answer_validation": {
                        "valid": False,
                        "reason": validation.reason,
                    },
                },
            )
        return AnswerGenerationResult(
            answer=answer,
            metadata={
                **metadata,
                "answer_validation": {
                    "valid": True,
                    "citations": validation.citations,
                },
            },
        )

    async def _call_provider(self, prompt: str) -> tuple[str, dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key or self._settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "provider": self._settings.llm_provider,
            "model": self._settings.llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self._settings.answer_temperature,
            "max_tokens": self._settings.answer_max_tokens,
        }
        started = time.perf_counter()
        client = self._client or httpx.AsyncClient(timeout=self._settings.llm_request_timeout_seconds)
        should_close = self._client is None
        try:
            response = await client.post(self._settings.llm_endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise AnswerGenerationError(str(exc)) from exc
        finally:
            if should_close:
                await client.aclose()

        answer = _extract_answer(data)
        if not answer.strip():
            raise AnswerGenerationError("answer provider returned an empty answer")
        metadata = {
            "provider": self._settings.llm_provider,
            "model": self._settings.llm_model,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "token_usage": data.get("usage", {}),
        }
        return answer.strip(), metadata


@dataclass(frozen=True)
class CitationValidation:
    valid: bool
    reason: str | None = None
    citations: list[int] = field(default_factory=list)


def build_answer_prompt(question: str, context_text: str, references: list[Reference]) -> str:
    allowed = ", ".join(f"[{ref.index}]" for ref in references)
    return (
        "You are RecallForge's knowledge answer generator.\n"
        "Use only the provided context and references.\n"
        "If the context is insufficient, answer exactly: 当前资料无法确认。\n"
        "Every factual claim must cite one or more allowed reference numbers.\n"
        f"Allowed reference numbers: {allowed or '<none>'}.\n"
        "Do not invent reference numbers.\n\n"
        f"Question:\n{question}\n\n"
        f"Context:\n{context_text}\n"
    )


def build_repair_prompt(original_prompt: str, answer: str, reason: str | None) -> str:
    return (
        f"{original_prompt}\n\n"
        "The previous answer failed citation validation.\n"
        f"Validation reason: {reason or 'unknown'}.\n"
        "Rewrite the answer using only allowed references, or return the exact refusal sentence.\n\n"
        f"Previous answer:\n{answer}\n"
    )


def validate_answer_citations(answer: str, references: list[Reference]) -> CitationValidation:
    stripped = answer.strip()
    if stripped == REFUSAL_ANSWER or stripped.startswith("当前资料无法确认"):
        return CitationValidation(valid=True, citations=[])

    allowed = {ref.index for ref in references}
    citations = [int(match.group(1)) for match in _CITATION_RE.finditer(answer)]
    invented = sorted(set(citations) - allowed)
    if invented:
        return CitationValidation(False, f"invented citation: {invented[0]}", citations)
    if references and not citations:
        return CitationValidation(False, "answer has no citations", citations)
    if not references:
        return CitationValidation(False, "answer has no references", citations)
    return CitationValidation(True, citations=citations)


def _extract_answer(data: dict[str, Any]) -> str:
    if isinstance(data.get("answer"), str):
        return data["answer"]
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(first.get("text"), str):
                return first["text"]
    output = data.get("output")
    if isinstance(output, dict):
        if isinstance(output.get("text"), str):
            return output["text"]
        if isinstance(output.get("answer"), str):
            return output["answer"]
    return ""
