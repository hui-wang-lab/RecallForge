"""Pydantic request and response models for the M5 Knowledge API."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ValidationApiError

FORBIDDEN_IDENTITY_FIELDS = {
    "tenant_id",
    "user_id",
    "department",
    "access_level",
    "status",
    "embedding_model",
    "embedding_provider",
    "embedding_dim",
}
ALLOWED_FILTER_FIELDS = {"doc_type", "source_uri", "version", "date_range", "knowledge_base_id", "knowledge_base_ids"}
ALLOWED_SEARCH_MODES = {"vector", "hybrid", "full_text"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def reject_forbidden_top_level(data: Any) -> Any:
    if isinstance(data, dict):
        forbidden = sorted(FORBIDDEN_IDENTITY_FIELDS & set(data))
        if forbidden:
            raise ValueError(f"request body contains forbidden identity or permission fields: {forbidden}")
    return data


def validate_client_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    if not filters:
        return {}
    forbidden = sorted(FORBIDDEN_IDENTITY_FIELDS & set(filters))
    if forbidden:
        raise ValueError(f"forbidden filter field: {forbidden[0]}")
    unknown = sorted(set(filters) - ALLOWED_FILTER_FIELDS)
    if unknown:
        raise ValueError(f"unknown filter field: {unknown[0]}")
    for key in ("doc_type", "source_uri"):
        if key in filters and (not isinstance(filters[key], str) or not filters[key].strip()):
            raise ValueError(f"filters.{key} must be a non-empty string")
    if "version" in filters and not isinstance(filters["version"], int):
        raise ValueError("filters.version must be an integer")
    if "knowledge_base_id" in filters:
        value = filters["knowledge_base_id"]
        if not isinstance(value, int) or value <= 0:
            raise ValueError("filters.knowledge_base_id must be a positive integer")
    if "knowledge_base_ids" in filters:
        value = filters["knowledge_base_ids"]
        if not isinstance(value, list) or not value:
            raise ValueError("filters.knowledge_base_ids must be a non-empty list")
        if not all(isinstance(item, int) and item > 0 for item in value):
            raise ValueError("filters.knowledge_base_ids must contain positive integers")
    if "knowledge_base_id" in filters and "knowledge_base_ids" in filters:
        raise ValueError("filters cannot contain both knowledge_base_id and knowledge_base_ids")
    return dict(filters)


def validate_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    forbidden = sorted(FORBIDDEN_IDENTITY_FIELDS & set(metadata))
    if forbidden:
        raise ValueError(f"metadata contains forbidden field: {forbidden[0]}")
    return dict(metadata)


class RetrieveRequest(StrictModel):
    question: str = Field(min_length=1)
    filters: dict[str, Any] = Field(default_factory=dict)
    top_k: int | None = Field(default=None, gt=0)
    final_top_k: int | None = Field(default=None, gt=0)
    search_mode: Literal["vector", "hybrid", "full_text"] = "vector"

    @model_validator(mode="before")
    @classmethod
    def _reject_identity_fields(cls, data: Any) -> Any:
        return reject_forbidden_top_level(data)

    @field_validator("filters")
    @classmethod
    def _validate_filters(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_client_filters(value)


class ContextRequest(RetrieveRequest):
    pass


class AnswerRequest(RetrieveRequest):
    pass


class RagQueryRequest(StrictModel):
    question: str = Field(min_length=1)
    filters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _reject_identity_fields(cls, data: Any) -> Any:
        return reject_forbidden_top_level(data)

    @field_validator("filters")
    @classmethod
    def _validate_filters(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_client_filters(value)


class DocumentUploadCommand:
    def __init__(
        self,
        *,
        file_path: Path,
        source_uri: str,
        source_name: str | None = None,
        doc_type: str | None = None,
        title: str | None = None,
        parser_hint: str = "auto",
        template_hint: str = "auto",
        metadata: dict[str, Any] | None = None,
        knowledge_base_id: int | None = None,
        cleanup_file: bool = True,
    ) -> None:
        self.file_path = file_path
        self.source_uri = source_uri
        self.source_name = source_name
        self.doc_type = doc_type
        self.title = title
        self.parser_hint = parser_hint
        self.template_hint = template_hint
        self.metadata = validate_metadata(metadata)
        self.knowledge_base_id = knowledge_base_id
        self.cleanup_file = cleanup_file


class ReferenceChildResponse(StrictModel):
    chunk_id: int
    chunk_key: str
    rerank_score: float
    rerank_rank: int
    page_start: int | None = None
    page_end: int | None = None


class ReferenceResponse(StrictModel):
    ref_id: str
    index: int
    document_id: int
    document_title: str | None = None
    chunk_id: int
    chunk_key: str
    parent_id: int
    parent_key: str
    source_uri: str
    doc_type: str
    page_start: int | None = None
    page_end: int | None = None
    heading_path: list[str] | None = None
    version: int
    rerank_score: float
    vector_score: float
    updated_at: datetime | None = None
    child_chunks: list[ReferenceChildResponse] = Field(default_factory=list)


class HitSummaryResponse(StrictModel):
    chunk_id: int
    document_id: int
    parent_id: int
    chunk_key: str
    parent_key: str
    vector_rank: int
    vector_score: float
    rerank_rank: int | None = None
    rerank_score: float | None = None
    score_source: str
    selected: bool = False
    content_snippet: str | None = None


class IngestJobResponse(StrictModel):
    job_id: uuid.UUID
    document_id: int | None = None
    status: str
    source_uri: str
    source_name: str | None = None
    doc_type: str | None = None
    parser_used: str | None = None
    chunker_used: str | None = None
    parent_chunk_count: int = 0
    child_chunk_count: int = 0
    warnings: list[Any] = Field(default_factory=list)
    parse_report: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class DocumentIngestResponse(StrictModel):
    document_id: int | None
    knowledge_base_id: int | None = None
    job_id: uuid.UUID
    status: str
    embedding_status: str
    trace_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrieveResponse(StrictModel):
    status: str
    references: list[ReferenceResponse] = Field(default_factory=list)
    hit_summary: list[HitSummaryResponse] = Field(default_factory=list)
    refusal_reason: str | None = None
    trace_id: str
    rewritten_query: str | None = None
    effective_query: str | None = None
    latencies_ms: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextResponse(RetrieveResponse):
    context_text: str = ""


class AnswerResponse(StrictModel):
    status: str
    answer: str
    references: list[ReferenceResponse] = Field(default_factory=list)
    refusal_reason: str | None = None
    trace_id: str
    hit_summary: list[HitSummaryResponse] = Field(default_factory=list)
    latencies_ms: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(StrictModel):
    status: str


class KnowledgeBaseCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    default_department: str = Field(default="global", min_length=1)
    default_access_level: Literal["public", "internal", "confidential", "restricted"] = "internal"
    default_doc_type: str | None = None
    default_parser: str = "auto"
    default_template: str = "auto"
    default_search_mode: Literal["vector", "hybrid", "full_text"] = "vector"
    default_top_k: int | None = Field(default=None, gt=0)
    default_final_top_k: int | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _reject_identity_fields(cls, data: Any) -> Any:
        return reject_forbidden_top_level(data)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_metadata(value)


class KnowledgeBaseUpdateRequest(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    tags: list[str] | None = None
    default_department: str | None = Field(default=None, min_length=1)
    default_access_level: Literal["public", "internal", "confidential", "restricted"] | None = None
    default_doc_type: str | None = None
    default_parser: str | None = None
    default_template: str | None = None
    default_search_mode: Literal["vector", "hybrid", "full_text"] | None = None
    default_top_k: int | None = Field(default=None, gt=0)
    default_final_top_k: int | None = Field(default=None, gt=0)
    metadata: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_identity_fields(cls, data: Any) -> Any:
        return reject_forbidden_top_level(data)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return validate_metadata(value) if value is not None else None


class KnowledgeBaseDeleteRequest(StrictModel):
    mode: Literal["archive", "delete"] = "archive"
    reason: str | None = None


class KnowledgeBaseResponse(StrictModel):
    knowledge_base_id: int
    name: str
    description: str | None = None
    status: str
    role: str | None = None
    tags: list[str] = Field(default_factory=list)
    default_department: str
    default_access_level: str
    default_doc_type: str | None = None
    default_parser: str
    default_template: str
    default_search_mode: str
    default_top_k: int | None = None
    default_final_top_k: int | None = None
    document_count: int = 0
    active_chunk_count: int = 0
    last_ingest_status: str | None = None
    last_query_at: datetime | None = None
    actions: dict[str, bool] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    trace_id: str


class KnowledgeBaseListResponse(StrictModel):
    items: list[KnowledgeBaseResponse] = Field(default_factory=list)
    trace_id: str


class DocumentSummaryResponse(StrictModel):
    document_id: int
    knowledge_base_id: int | None = None
    source_uri: str
    source_name: str | None = None
    title: str | None = None
    doc_type: str
    version: int
    status: str
    content_hash: str
    department: str
    access_level: str
    parent_chunk_count: int = 0
    child_chunk_count: int = 0
    embedding_status: str = "unknown"
    last_ingest_job_id: uuid.UUID | None = None
    last_ingest_status: str | None = None
    warning_count: int = 0
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DocumentListResponse(StrictModel):
    items: list[DocumentSummaryResponse] = Field(default_factory=list)
    trace_id: str


class DocumentUpdateRequest(StrictModel):
    title: str | None = None
    source_name: str | None = None
    doc_type: str | None = None
    metadata: dict[str, Any] | None = None
    department: str | None = None
    access_level: Literal["public", "internal", "confidential", "restricted"] | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_server_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            forbidden = sorted((FORBIDDEN_IDENTITY_FIELDS | {"content_hash", "version"}) & set(data))
            if forbidden:
                raise ValueError(f"document patch contains forbidden server field: {forbidden[0]}")
        return data

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return validate_metadata(value) if value is not None else None


class DocumentDeleteResponse(StrictModel):
    document_id: int
    knowledge_base_id: int
    status: str
    vector_delete_status: str
    trace_id: str


class ReindexRequest(StrictModel):
    dry_run: bool = True
    document_ids: list[int] = Field(default_factory=list)
    embedding_model: str | None = None
    force: bool = False
    limit: int | None = Field(default=None, gt=0)
    reason: str | None = None


class ReindexResponse(StrictModel):
    knowledge_base_id: int
    dry_run: bool
    estimated_documents: int
    status: str
    trace_id: str


def raise_validation_error(message: str) -> None:
    raise ValidationApiError("validation_error", message)
