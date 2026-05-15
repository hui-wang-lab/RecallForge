from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_m6_routes_do_not_bypass_service_boundaries():
    route = (ROOT / "recallforge" / "api" / "routes" / "knowledge_bases.py").read_text(encoding="utf-8")

    assert "PgVectorStore" not in route
    assert ".search(" not in route
    assert ".embed_query(" not in route
    assert "parse_to_chunk_package" not in route
    assert "VectorStoreAdapter" not in route


def test_m6_ui_does_not_expose_identity_inputs():
    static_dir = ROOT / "recallforge" / "console" / "static"
    text = "\n".join(path.read_text(encoding="utf-8") for path in static_dir.glob("*.*"))

    for field in ("tenant_id", "user_id", "access_level"):
        assert f'name="{field}"' not in text
