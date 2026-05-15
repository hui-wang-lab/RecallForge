from __future__ import annotations

import pytest
from pydantic import ValidationError

from recallforge.api.schemas import RagQueryRequest, RetrieveRequest


def test_rag_query_accepts_only_question_and_filters():
    payload = RagQueryRequest(question="refund policy", filters={"doc_type": "markdown", "version": 2})

    assert payload.question == "refund policy"
    assert payload.filters["version"] == 2


def test_rag_query_rejects_top_k_and_identity_fields():
    with pytest.raises(ValidationError):
        RagQueryRequest(question="refund policy", filters={}, top_k=10)

    with pytest.raises(ValidationError):
        RagQueryRequest(question="refund policy", tenant_id="tenant-a", filters={})


def test_filter_whitelist_blocks_permission_fields():
    with pytest.raises(ValidationError, match="forbidden filter field"):
        RetrieveRequest(question="refund policy", filters={"tenant_id": "*"})

    with pytest.raises(ValidationError, match="forbidden filter field"):
        RetrieveRequest(question="refund policy", filters={"status": "active"})

    with pytest.raises(ValidationError, match="unknown filter field"):
        RetrieveRequest(question="refund policy", filters={"chunk_id": 1})
