"""Add context budget records.

Revision ID: 20260627_0016
Revises: 20260627_0015
Create Date: 2026-06-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260627_0016"
down_revision: str | None = "20260627_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_budget_ledgers",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("total_input_tokens", sa.Integer(), nullable=False),
        sa.Column("total_output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_reasoning_tokens", sa.Integer(), nullable=False),
        sa.Column("active_seconds", sa.Integer(), nullable=False),
        sa.Column("model_call_count", sa.Integer(), nullable=False),
        sa.Column("threshold_status", sa.String(length=64), nullable=False),
        sa.Column("active_window_started_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(
        "ix_run_budget_ledgers_threshold_status",
        "run_budget_ledgers",
        ["threshold_status"],
    )
    op.create_table(
        "context_compactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True)),
        sa.Column("graph_name", sa.String(length=128), nullable=False),
        sa.Column("graph_version", sa.Integer(), nullable=False),
        sa.Column("before_estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("after_estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("artifact_refs", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_context_compactions_run_id", "context_compactions", ["run_id"])
    op.create_index(
        "ix_context_compactions_agent_id",
        "context_compactions",
        ["agent_id"],
    )
    op.create_index(
        "ix_context_compactions_graph_name",
        "context_compactions",
        ["graph_name"],
    )
    op.create_index(
        "ix_context_compactions_created_at",
        "context_compactions",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_context_compactions_created_at", table_name="context_compactions")
    op.drop_index("ix_context_compactions_graph_name", table_name="context_compactions")
    op.drop_index("ix_context_compactions_agent_id", table_name="context_compactions")
    op.drop_index("ix_context_compactions_run_id", table_name="context_compactions")
    op.drop_table("context_compactions")
    op.drop_index(
        "ix_run_budget_ledgers_threshold_status",
        table_name="run_budget_ledgers",
    )
    op.drop_table("run_budget_ledgers")
