"""Add durable approval records.

Revision ID: 20260626_0009
Revises: 20260626_0008
Create Date: 2026-06-26 00:09:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260626_0009"
down_revision = "20260626_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "tool_invocation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("tool_call_id", sa.String(length=255), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("tool_version", sa.String(length=32), nullable=False),
        sa.Column("canonical_arguments", postgresql.JSONB(), nullable=False),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("workspace_path", sa.Text(), nullable=False),
        sa.Column("workspace_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tool_invocation_id"],
            ["tool_invocations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approvals_run_id", "approvals", ["run_id"])
    op.create_index("ix_approvals_agent_id", "approvals", ["agent_id"])
    op.create_index(
        "ix_approvals_tool_invocation_id",
        "approvals",
        ["tool_invocation_id"],
    )
    op.create_index("ix_approvals_tool_name", "approvals", ["tool_name"])
    op.create_index("ix_approvals_status", "approvals", ["status"])
    op.create_index("ix_approvals_expires_at", "approvals", ["expires_at"])
    op.create_index("ix_approvals_created_at", "approvals", ["created_at"])
    op.create_index(
        "uq_approvals_run_tool_call",
        "approvals",
        ["run_id", "tool_call_id"],
        unique=True,
    )
    op.create_index(
        "ix_approvals_status_expires_at",
        "approvals",
        ["status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_approvals_status_expires_at", table_name="approvals")
    op.drop_index("uq_approvals_run_tool_call", table_name="approvals")
    op.drop_index("ix_approvals_created_at", table_name="approvals")
    op.drop_index("ix_approvals_expires_at", table_name="approvals")
    op.drop_index("ix_approvals_status", table_name="approvals")
    op.drop_index("ix_approvals_tool_name", table_name="approvals")
    op.drop_index("ix_approvals_tool_invocation_id", table_name="approvals")
    op.drop_index("ix_approvals_agent_id", table_name="approvals")
    op.drop_index("ix_approvals_run_id", table_name="approvals")
    op.drop_table("approvals")
