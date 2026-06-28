"""Add team child result records.

Revision ID: 20260627_0018
Revises: 20260627_0017
Create Date: 2026-06-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260627_0018"
down_revision: str | None = "20260627_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "team_child_results",
        sa.Column("child_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("root_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("patch_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("changed_files", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_artifact_refs", postgresql.JSONB(), nullable=False),
        sa.Column("failure_kind", sa.String(length=64)),
        sa.Column("patch_aggregated", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["team_assignments.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["child_run_id"],
            ["runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patch_artifact_id"],
            ["artifacts.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("child_run_id"),
    )
    op.create_index(
        "ix_team_child_results_assignment_id",
        "team_child_results",
        ["assignment_id"],
    )
    op.create_index(
        "ix_team_child_results_parent_run_id",
        "team_child_results",
        ["parent_run_id"],
    )
    op.create_index(
        "ix_team_child_results_root_run_id",
        "team_child_results",
        ["root_run_id"],
    )
    op.create_index("ix_team_child_results_status", "team_child_results", ["status"])
    op.create_index(
        "ix_team_child_results_patch_artifact_id",
        "team_child_results",
        ["patch_artifact_id"],
    )
    op.create_index(
        "ix_team_child_results_failure_kind",
        "team_child_results",
        ["failure_kind"],
    )
    op.create_index(
        "ix_team_child_results_patch_aggregated",
        "team_child_results",
        ["patch_aggregated"],
    )
    op.create_index(
        "ix_team_child_results_created_at",
        "team_child_results",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_team_child_results_created_at", table_name="team_child_results")
    op.drop_index(
        "ix_team_child_results_patch_aggregated",
        table_name="team_child_results",
    )
    op.drop_index("ix_team_child_results_failure_kind", table_name="team_child_results")
    op.drop_index(
        "ix_team_child_results_patch_artifact_id",
        table_name="team_child_results",
    )
    op.drop_index("ix_team_child_results_status", table_name="team_child_results")
    op.drop_index("ix_team_child_results_root_run_id", table_name="team_child_results")
    op.drop_index(
        "ix_team_child_results_parent_run_id",
        table_name="team_child_results",
    )
    op.drop_index(
        "ix_team_child_results_assignment_id",
        table_name="team_child_results",
    )
    op.drop_table("team_child_results")
