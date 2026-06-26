"""Add validation reports.

Revision ID: 20260626_0011
Revises: 20260626_0010
Create Date: 2026-06-26 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260626_0011"
down_revision: str | None = "20260626_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "validation_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_validation_reports_agent_id"),
        "validation_reports",
        ["agent_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_validation_reports_attempt"),
        "validation_reports",
        ["attempt"],
        unique=False,
    )
    op.create_index(
        op.f("ix_validation_reports_created_at"),
        "validation_reports",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_validation_reports_run_id"),
        "validation_reports",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_validation_reports_status"),
        "validation_reports",
        ["status"],
        unique=False,
    )
    op.create_table(
        "validation_gate_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gate_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("command", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("stdout_summary", sa.Text(), nullable=False),
        sa.Column("stderr_summary", sa.Text(), nullable=False),
        sa.Column(
            "artifact_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("failure_kind", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["validation_reports.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_validation_gate_results_created_at"),
        "validation_gate_results",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_validation_gate_results_failure_kind"),
        "validation_gate_results",
        ["failure_kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_validation_gate_results_gate_id"),
        "validation_gate_results",
        ["gate_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_validation_gate_results_report_id"),
        "validation_gate_results",
        ["report_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_validation_gate_results_run_id"),
        "validation_gate_results",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_validation_gate_results_status"),
        "validation_gate_results",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_validation_gate_results_status"),
        table_name="validation_gate_results",
    )
    op.drop_index(
        op.f("ix_validation_gate_results_run_id"),
        table_name="validation_gate_results",
    )
    op.drop_index(
        op.f("ix_validation_gate_results_report_id"),
        table_name="validation_gate_results",
    )
    op.drop_index(
        op.f("ix_validation_gate_results_gate_id"),
        table_name="validation_gate_results",
    )
    op.drop_index(
        op.f("ix_validation_gate_results_failure_kind"),
        table_name="validation_gate_results",
    )
    op.drop_index(
        op.f("ix_validation_gate_results_created_at"),
        table_name="validation_gate_results",
    )
    op.drop_table("validation_gate_results")
    op.drop_index(op.f("ix_validation_reports_status"), table_name="validation_reports")
    op.drop_index(op.f("ix_validation_reports_run_id"), table_name="validation_reports")
    op.drop_index(
        op.f("ix_validation_reports_created_at"),
        table_name="validation_reports",
    )
    op.drop_index(
        op.f("ix_validation_reports_attempt"),
        table_name="validation_reports",
    )
    op.drop_index(
        op.f("ix_validation_reports_agent_id"),
        table_name="validation_reports",
    )
    op.drop_table("validation_reports")
