"""Tests for Alembic migrations defined in M1."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from alembic.config import Config as AlembicConfig

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
VERSIONS_DIR = MIGRATIONS_DIR / "versions"


# ── Migration file existence ────────────────────────────────────


class TestMigrationFileExists:
    def test_versions_directory_exists(self):
        assert VERSIONS_DIR.is_dir()

    def test_initial_migration_file_exists(self):
        files = list(VERSIONS_DIR.glob("*.py"))
        assert len(files) >= 1, "No migration files found in versions directory"
        # Check at least one file contains the initial create
        found = False
        for f in files:
            content = f.read_text(encoding="utf-8")
            if "rag_documents" in content and "rag_chunks" in content:
                found = True
                break
        assert found, "No migration file creating rag_documents and rag_chunks"


# ── Alembic heads ───────────────────────────────────────────────


class TestAlembicHeads:
    def test_single_head(self):
        """Verify alembic heads returns a single head (no branching)."""
        from alembic.script import ScriptDirectory

        cfg = AlembicConfig(str(MIGRATIONS_DIR.parent / "alembic.ini"))
        cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
        assert len(heads) == 1, f"Expected single head, got: {heads}"
        assert heads[0] == "0003"


# ── Migration content checks ────────────────────────────────────


class TestMigrationContent:
    @pytest.fixture()
    def migration_source(self) -> str:
        files = list(VERSIONS_DIR.glob("*.py"))
        assert files, "No migration files"
        # Find the initial migration
        for f in files:
            content = f.read_text(encoding="utf-8")
            if "CREATE EXTENSION IF NOT EXISTS vector" in content or "rag_documents" in content:
                return content
        pytest.fail("Initial migration not found")

    def test_creates_vector_extension(self, migration_source: str):
        assert "CREATE EXTENSION IF NOT EXISTS vector" in migration_source

    def test_creates_five_tables(self, migration_source: str):
        for table in [
            "rag_documents",
            "rag_parent_chunks",
            "rag_chunks",
            "rag_ingest_jobs",
            "rag_query_logs",
        ]:
            assert table in migration_source, f"Table {table} not found in migration"

    def test_no_hnsw_index(self, migration_source: str):
        assert "hnsw" not in migration_source.lower(), "Migration must not contain HNSW indexes"

    def test_has_vector_1024_column(self, migration_source: str):
        assert "embedding_text_embedding_v4_1024" in migration_source

    def test_has_content_tsv(self, migration_source: str):
        assert "content_tsv" in migration_source

    def test_has_embedding_metadata(self, migration_source: str):
        assert "embedding_metadata" in migration_source

    def test_has_job_id_unique(self, migration_source: str):
        assert "uq_rag_ingest_jobs_job_id" in migration_source

    def test_has_active_source_partial_unique(self, migration_source: str):
        assert "uq_rag_documents_active_source" in migration_source

    def test_has_downgrade(self, migration_source: str):
        assert "def downgrade()" in migration_source

    def test_downgrade_drops_tables(self, migration_source: str):
        assert "drop_table" in migration_source

    def test_downgrade_does_not_drop_vector_extension(self, migration_source: str):
        """The downgrade should NOT contain an active DROP EXTENSION vector."""
        lines = migration_source.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("op.execute") and "DROP EXTENSION" in stripped:
                pytest.fail("Downgrade should not drop vector extension by default")

    def test_has_gin_index(self, migration_source: str):
        assert "gin" in migration_source.lower()

    def test_revision_and_down_revision(self, migration_source: str):
        assert 'revision = "' in migration_source or "revision = '" in migration_source
        assert "down_revision = None" in migration_source


# ── Module import check ─────────────────────────────────────────


class TestMigrationModuleImport:
    def test_can_import_migration_module(self):
        files = list(VERSIONS_DIR.glob("*.py"))
        for f in files:
            module_name = f.stem
            # Skip __pycache__
            if module_name.startswith("__"):
                continue
            mod = importlib.import_module(f"migrations.versions.{module_name}")
            assert hasattr(mod, "revision")
            assert hasattr(mod, "upgrade")
            assert hasattr(mod, "downgrade")
            if mod.revision == "0001":
                assert mod.down_revision is None
            else:
                assert mod.down_revision is not None


class TestM6MigrationContent:
    @pytest.fixture()
    def migration_source(self) -> str:
        path = VERSIONS_DIR / "0003_add_knowledge_base_governance.py"
        assert path.exists()
        return path.read_text(encoding="utf-8")

    def test_creates_knowledge_base_tables(self, migration_source: str):
        for table in [
            "rag_knowledge_bases",
            "rag_knowledge_base_members",
            "rag_application_grants",
            "rag_audit_events",
        ]:
            assert table in migration_source

    def test_adds_knowledge_base_scope_to_existing_tables(self, migration_source: str):
        for table in [
            "rag_documents",
            "rag_parent_chunks",
            "rag_chunks",
            "rag_ingest_jobs",
            "rag_query_logs",
        ]:
            assert table in migration_source
        assert "knowledge_base_id" in migration_source
        assert "knowledge_base_ids" in migration_source
