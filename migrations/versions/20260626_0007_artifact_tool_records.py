"""Add artifact metadata and durable tool invocation records.

Revision ID: 20260626_0007
Revises: 20260626_0006
Create Date: 2026-06-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260626_0007"
down_revision: str | None = "20260626_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])
    op.create_index("ix_artifacts_agent_id", "artifacts", ["agent_id"])
    op.create_index("ix_artifacts_artifact_type", "artifacts", ["artifact_type"])
    op.create_index("ix_artifacts_created_at", "artifacts", ["created_at"])

    op.create_table(
        "tool_invocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("tool_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("arguments_hash", sa.String(64), nullable=False),
        sa.Column("risk_level", sa.String(32), nullable=False),
        sa.Column(
            "path_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "preimage_hashes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "expected_postimage_hashes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column(
            "artifact_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tool_invocations_run_id", "tool_invocations", ["run_id"])
    op.create_index("ix_tool_invocations_agent_id", "tool_invocations", ["agent_id"])
    op.create_index(
        "ix_tool_invocations_tool_name",
        "tool_invocations",
        ["tool_name"],
    )
    op.create_index("ix_tool_invocations_status", "tool_invocations", ["status"])
    op.create_index(
        "ix_tool_invocations_created_at",
        "tool_invocations",
        ["created_at"],
    )
    op.create_index(
        "uq_tool_invocations_run_idempotency",
        "tool_invocations",
        ["run_id", "idempotency_key"],
        unique=True,
    )
    op.execute(
        """
        UPDATE runs
        SET graph_name = 'solo-modifying', graph_version = 1
        WHERE execution_kind = 'coding'
          AND intent = 'modifying'
          AND dispatch_status IN ('queued', 'retry_scheduled')
          AND graph_name IS NULL
          AND graph_version IS NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE runs
        SET graph_name = NULL, graph_version = NULL
        WHERE execution_kind = 'coding'
          AND intent = 'modifying'
          AND graph_name = 'solo-modifying'
          AND graph_version = 1
        """
    )
    op.drop_index("uq_tool_invocations_run_idempotency", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_created_at", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_status", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_tool_name", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_agent_id", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_run_id", table_name="tool_invocations")
    op.drop_table("tool_invocations")
    op.drop_index("ix_artifacts_created_at", table_name="artifacts")
    op.drop_index("ix_artifacts_artifact_type", table_name="artifacts")
    op.drop_index("ix_artifacts_agent_id", table_name="artifacts")
    op.drop_index("ix_artifacts_run_id", table_name="artifacts")
    op.drop_table("artifacts")
