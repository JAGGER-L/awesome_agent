"""Add durable run cancellation request fields.

Revision ID: 20260626_0010
Revises: 20260626_0009
Create Date: 2026-06-26 00:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260626_0010"
down_revision = "20260626_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("cancel_requested_by", sa.String(length=255), nullable=True),
    )
    op.add_column("runs", sa.Column("cancel_reason", sa.Text(), nullable=True))
    op.create_index(
        "ix_runs_cancel_requested_at",
        "runs",
        ["cancel_requested_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_runs_cancel_requested_at", table_name="runs")
    op.drop_column("runs", "cancel_reason")
    op.drop_column("runs", "cancel_requested_by")
    op.drop_column("runs", "cancel_requested_at")
