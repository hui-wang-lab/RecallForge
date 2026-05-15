from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_api_routes_do_not_bypass_retrieval_or_vector_boundaries():
    route_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "recallforge" / "api" / "routes").glob("*.py"))

    assert "recallforge.storage.pgvector_store" not in route_text
    assert ".search(" not in route_text
    assert ".embed_query(" not in route_text
    assert ".embed_documents(" not in route_text


def test_knowledge_service_does_not_parse_or_search_vectors_directly():
    text = (ROOT / "recallforge" / "api" / "knowledge_service.py").read_text(encoding="utf-8")

    assert "parse_to_chunk_package" not in text
    assert "VectorStoreAdapter" not in text
    assert ".search(" not in text


def test_answering_has_no_hardcoded_baseline_model_names():
    text = (ROOT / "recallforge" / "api" / "answering.py").read_text(encoding="utf-8")

    assert "qwen" not in text.lower()
    assert "text-embedding" not in text.lower()
