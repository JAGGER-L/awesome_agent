"""Add workspace retention fields.

Revision ID: 20260627_0014
Revises: 20260627_0013
Create Date: 2026-06-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260627_0014"
down_revision: str | None = "20260627_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "workspace_retention_status",
            sa.String(length=32),
            nullable=False,
            server_default="retained",
        ),
    )
    op.add_column(
        "runs",
        sa.Column("workspace_cleaned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("workspace_cleanup_reason", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_runs_workspace_retention_status",
        "runs",
        ["workspace_retention_status"],
    )
    op.alter_column("runs", "workspace_retention_status", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_runs_workspace_retention_status", table_name="runs")
    op.drop_column("runs", "workspace_cleanup_reason")
    op.drop_column("runs", "workspace_cleaned_at")
    op.drop_column("runs", "workspace_retention_status")
