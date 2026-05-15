"""Add 'retrieved' to rag_query_logs status check constraint.

The initial migration 0001 already included 'retrieved' in the model
definition, but the database was created from an earlier revision that
only allowed ('success', 'refused', 'failed'). This migration brings
the live constraint in sync with the code.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_rag_query_logs_status", "rag_query_logs", type_="check")
    op.create_check_constraint(
        "ck_rag_query_logs_status",
        "rag_query_logs",
        "status IN ('success', 'retrieved', 'refused', 'failed')",
    )

    op.drop_constraint("ck_rag_query_logs_status_payload", "rag_query_logs", type_="check")
    op.create_check_constraint(
        "ck_rag_query_logs_status_payload",
        "rag_query_logs",
        "(status = 'success' AND answer IS NOT NULL) "
        "OR (status = 'retrieved' AND answer IS NULL) "
        "OR (status = 'refused' AND refusal_reason IS NOT NULL) "
        "OR (status = 'failed' AND error_message IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_rag_query_logs_status_payload", "rag_query_logs", type_="check")
    op.create_check_constraint(
        "ck_rag_query_logs_status_payload",
        "rag_query_logs",
        "(status = 'success' AND answer IS NOT NULL) "
        "OR (status = 'refused' AND refusal_reason IS NOT NULL) "
        "OR (status = 'failed' AND error_message IS NOT NULL)",
    )

    op.drop_constraint("ck_rag_query_logs_status", "rag_query_logs", type_="check")
    op.create_check_constraint(
        "ck_rag_query_logs_status",
        "rag_query_logs",
        "status IN ('success', 'refused', 'failed')",
    )
