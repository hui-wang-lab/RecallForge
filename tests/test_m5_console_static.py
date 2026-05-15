from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from recallforge.api.app import create_app
from recallforge.config import Settings


STATIC_ROOT = Path(__file__).resolve().parents[1] / "recallforge" / "console" / "static"


def test_console_static_does_not_expose_permission_inputs():
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    combined = f"{html}\n{js}"

    assert "tenant_id" not in combined
    assert "department" not in combined
    assert "access_level" not in combined
    assert "localStorage" not in combined
    assert "/api/knowledge/" in js


def test_console_requires_scope_when_auth_enabled():
    settings = Settings(openai_api_key="test", console_enabled=True, api_require_auth=True)
    response = TestClient(create_app(settings)).get("/console")

    assert response.status_code == 401
