from __future__ import annotations

import json
import uuid

import pytest

from recallforge.api.auth import authenticate_request
from recallforge.api.errors import AuthenticationError, PermissionDeniedError
from recallforge.config import Settings


def test_api_key_maps_to_fixed_request_context_and_scopes():
    request_id = str(uuid.uuid4())
    settings = Settings(
        openai_api_key="test",
        api_service_keys=json.dumps(
            {
                "secret-key": {
                    "key_id": "ingest-worker",
                    "tenant_id": "tenant-a",
                    "user_id": "svc-ingest",
                    "department": "global",
                    "access_level": "restricted",
                    "scopes": ["documents:write", "knowledge:read"],
                }
            }
        ),
    )

    auth = authenticate_request(
        settings,
        authorization=None,
        x_api_key="secret-key",
        x_request_id=request_id,
    )

    assert auth.context.tenant_id == "tenant-a"
    assert auth.context.user_id == "svc-ingest"
    assert auth.context.request_id == uuid.UUID(request_id)
    assert auth.scopes == frozenset({"documents:write", "knowledge:read"})
    assert auth.key_id == "ingest-worker"


def test_invalid_api_key_and_access_level_are_rejected():
    settings = Settings(openai_api_key="test", api_service_keys="{}")

    with pytest.raises(AuthenticationError):
        authenticate_request(settings, authorization=None, x_api_key="missing", x_request_id=None)

    bad_settings = Settings(
        openai_api_key="test",
        api_service_keys=json.dumps(
            {
                "secret-key": {
                    "tenant_id": "tenant-a",
                    "user_id": "svc",
                    "department": "global",
                    "access_level": "root",
                    "scopes": ["documents:write"],
                }
            }
        ),
    )
    with pytest.raises(PermissionDeniedError):
        authenticate_request(bad_settings, authorization=None, x_api_key="secret-key", x_request_id=None)
