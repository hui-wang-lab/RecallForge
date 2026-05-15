"""add knowledge-base governance tables and scope fields

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-14
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rag_knowledge_bases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("owner_user_id", sa.Text(), nullable=False),
        sa.Column("default_department", sa.Text(), server_default=sa.text("'global'"), nullable=False),
        sa.Column("default_access_level", sa.Text(), server_default=sa.text("'internal'"), nullable=False),
        sa.Column("default_doc_type", sa.Text(), nullable=True),
        sa.Column("default_parser", sa.Text(), server_default=sa.text("'auto'"), nullable=False),
        sa.Column("default_template", sa.Text(), server_default=sa.text("'auto'"), nullable=False),
        sa.Column("default_search_mode", sa.Text(), server_default=sa.text("'vector'"), nullable=False),
        sa.Column("default_top_k", sa.Integer(), nullable=True),
        sa.Column("default_final_top_k", sa.Integer(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("reranker_model", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active', 'archived', 'deleted')", name="ck_rag_knowledge_bases_status"),
        sa.CheckConstraint(
            "default_access_level IN ('public', 'internal', 'confidential', 'restricted')",
            name="ck_rag_knowledge_bases_default_access_level",
        ),
        sa.CheckConstraint(
            "default_search_mode IN ('vector', 'full_text', 'hybrid')",
            name="ck_rag_knowledge_bases_default_search_mode",
        ),
        sa.CheckConstraint("default_top_k IS NULL OR default_top_k > 0", name="ck_rag_knowledge_bases_top_k"),
        sa.CheckConstraint(
            "default_final_top_k IS NULL OR default_final_top_k > 0",
            name="ck_rag_knowledge_bases_final_top_k",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_rag_kb_tenant_status_updated",
        "rag_knowledge_bases",
        ["tenant_id", "status", sa.text("updated_at DESC")],
    )
    op.create_index(
        "uq_rag_kb_tenant_active_name",
        "rag_knowledge_bases",
        ["tenant_id", "name"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "rag_knowledge_base_members",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("knowledge_base_id", sa.BigInteger(), nullable=False),
        sa.Column("principal_type", sa.Text(), nullable=False),
        sa.Column("principal_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "principal_type IN ('user', 'department', 'service', 'application')",
            name="ck_rag_kb_members_principal_type",
        ),
        sa.CheckConstraint("role IN ('owner', 'admin', 'editor', 'viewer', 'auditor')", name="ck_rag_kb_members_role"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["rag_knowledge_bases.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "tenant_id",
            "knowledge_base_id",
            "principal_type",
            "principal_id",
            name="uq_rag_kb_members_principal",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_rag_kb_members_principal",
        "rag_knowledge_base_members",
        ["tenant_id", "principal_type", "principal_id"],
    )
    op.create_index(
        "idx_rag_kb_members_kb_role",
        "rag_knowledge_base_members",
        ["tenant_id", "knowledge_base_id", "role"],
    )

    op.create_table(
        "rag_application_grants",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("application_id", sa.Text(), nullable=False),
        sa.Column("knowledge_base_id", sa.BigInteger(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_rag_application_grants_status"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["rag_knowledge_bases.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_rag_application_grants_app",
        "rag_application_grants",
        ["tenant_id", "application_id", "status"],
    )
    op.create_index(
        "idx_rag_application_grants_kb",
        "rag_application_grants",
        ["tenant_id", "knowledge_base_id", "status"],
    )

    op.create_table(
        "rag_audit_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", postgresql.UUID(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("knowledge_base_id", sa.BigInteger(), nullable=True),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.Column("job_id", postgresql.UUID(), nullable=True),
        sa.Column("request_id", postgresql.UUID(), nullable=True),
        sa.Column("actor_user_id", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("outcome IN ('success', 'denied', 'failed')", name="ck_rag_audit_events_outcome"),
        sa.UniqueConstraint("event_id", name="uq_rag_audit_events_event_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_rag_audit_tenant_kb_created",
        "rag_audit_events",
        ["tenant_id", "knowledge_base_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_rag_audit_tenant_action_created",
        "rag_audit_events",
        ["tenant_id", "action", sa.text("created_at DESC")],
    )

    for table in ("rag_documents", "rag_parent_chunks", "rag_chunks", "rag_ingest_jobs"):
        op.add_column(table, sa.Column("knowledge_base_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_knowledge_base_id",
            table,
            "rag_knowledge_bases",
            ["knowledge_base_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.add_column("rag_query_logs", sa.Column("knowledge_base_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "rag_query_logs",
        sa.Column(
            "knowledge_base_ids",
            postgresql.ARRAY(sa.BigInteger()),
            server_default=sa.text("'{}'::bigint[]"),
            nullable=False,
        ),
    )

    op.execute(
        """
        INSERT INTO rag_knowledge_bases (
            tenant_id, name, owner_user_id, default_department, default_access_level, created_by, updated_by
        )
        SELECT DISTINCT tenant_id, 'Default Knowledge Base', 'migration', 'global', 'internal', 'migration', 'migration'
        FROM rag_documents
        ON CONFLICT DO NOTHING
        """
    )
    for table in ("rag_documents", "rag_parent_chunks", "rag_chunks", "rag_ingest_jobs"):
        op.execute(
            f"""
            UPDATE {table} t
            SET knowledge_base_id = kb.id
            FROM rag_knowledge_bases kb
            WHERE t.tenant_id = kb.tenant_id
              AND kb.name = 'Default Knowledge Base'
              AND t.knowledge_base_id IS NULL
            """
        )
    op.execute(
        """
        UPDATE rag_query_logs q
        SET knowledge_base_id = kb.id,
            knowledge_base_ids = ARRAY[kb.id]::bigint[]
        FROM rag_knowledge_bases kb
        WHERE q.tenant_id = kb.tenant_id
          AND kb.name = 'Default Knowledge Base'
          AND q.knowledge_base_id IS NULL
        """
    )

    op.drop_index("uq_rag_documents_active_source", table_name="rag_documents")
    op.drop_constraint("uq_rag_documents_source_version", "rag_documents", type_="unique")
    op.create_unique_constraint(
        "uq_rag_documents_source_version",
        "rag_documents",
        ["tenant_id", "knowledge_base_id", "source_uri", "version"],
    )
    op.create_index(
        "uq_rag_documents_active_source",
        "rag_documents",
        ["tenant_id", "knowledge_base_id", "source_uri"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "idx_rag_documents_kb_status_updated",
        "rag_documents",
        ["tenant_id", "knowledge_base_id", "status", sa.text("updated_at DESC")],
    )
    op.create_index(
        "idx_rag_parent_chunks_kb_status",
        "rag_parent_chunks",
        ["tenant_id", "knowledge_base_id", "status"],
    )
    op.create_index(
        "idx_rag_chunks_kb_permission_active",
        "rag_chunks",
        ["tenant_id", "knowledge_base_id", "department", "access_level", "doc_type", "status", "version"],
    )
    op.create_index(
        "idx_rag_ingest_jobs_kb_status_created",
        "rag_ingest_jobs",
        ["tenant_id", "knowledge_base_id", "status", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_rag_query_logs_kb_created",
        "rag_query_logs",
        ["tenant_id", "knowledge_base_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_rag_query_logs_kb_created", table_name="rag_query_logs")
    op.drop_index("idx_rag_ingest_jobs_kb_status_created", table_name="rag_ingest_jobs")
    op.drop_index("idx_rag_chunks_kb_permission_active", table_name="rag_chunks")
    op.drop_index("idx_rag_parent_chunks_kb_status", table_name="rag_parent_chunks")
    op.drop_index("idx_rag_documents_kb_status_updated", table_name="rag_documents")
    op.drop_index("uq_rag_documents_active_source", table_name="rag_documents")
    op.drop_constraint("uq_rag_documents_source_version", "rag_documents", type_="unique")
    op.create_unique_constraint(
        "uq_rag_documents_source_version",
        "rag_documents",
        ["tenant_id", "source_uri", "version"],
    )
    op.create_index(
        "uq_rag_documents_active_source",
        "rag_documents",
        ["tenant_id", "source_uri"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.drop_column("rag_query_logs", "knowledge_base_ids")
    op.drop_column("rag_query_logs", "knowledge_base_id")
    for table in ("rag_ingest_jobs", "rag_chunks", "rag_parent_chunks", "rag_documents"):
        op.drop_constraint(f"fk_{table}_knowledge_base_id", table, type_="foreignkey")
        op.drop_column(table, "knowledge_base_id")

    op.drop_index("idx_rag_audit_tenant_action_created", table_name="rag_audit_events")
    op.drop_index("idx_rag_audit_tenant_kb_created", table_name="rag_audit_events")
    op.drop_table("rag_audit_events")
    op.drop_index("idx_rag_application_grants_kb", table_name="rag_application_grants")
    op.drop_index("idx_rag_application_grants_app", table_name="rag_application_grants")
    op.drop_table("rag_application_grants")
    op.drop_index("idx_rag_kb_members_kb_role", table_name="rag_knowledge_base_members")
    op.drop_index("idx_rag_kb_members_principal", table_name="rag_knowledge_base_members")
    op.drop_table("rag_knowledge_base_members")
    op.drop_index("uq_rag_kb_tenant_active_name", table_name="rag_knowledge_bases")
    op.drop_index("idx_rag_kb_tenant_status_updated", table_name="rag_knowledge_bases")
    op.drop_table("rag_knowledge_bases")
