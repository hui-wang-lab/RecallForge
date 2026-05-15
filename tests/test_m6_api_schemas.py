from __future__ import annotations

import pytest
from pydantic import ValidationError

from recallforge.api.schemas import DocumentUpdateRequest, KnowledgeBaseCreateRequest, RetrieveRequest


def test_kb_create_rejects_identity_fields():
    with pytest.raises(ValidationError):
        KnowledgeBaseCreateRequest(name="Product", tenant_id="evil")  # type: ignore[call-arg]


def test_retrieve_filters_accept_knowledge_base_scope():
    request = RetrieveRequest(question="refund", filters={"knowledge_base_ids": [1, 2]})
    assert request.filters["knowledge_base_ids"] == [1, 2]


def test_retrieve_filters_still_reject_permission_fields():
    with pytest.raises(ValidationError):
        RetrieveRequest(question="refund", filters={"tenant_id": "*"})


def test_document_patch_rejects_server_fields():
    with pytest.raises(ValidationError):
        DocumentUpdateRequest(content_hash="a" * 64)  # type: ignore[call-arg]
