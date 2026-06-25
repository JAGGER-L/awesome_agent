"""Add Run dispatch and lease fields.

Revision ID: 20260625_0004
Revises: 20260625_0003
Create Date: 2026-06-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260625_0004"
down_revision: str | None = "20260625_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
    )
    op.add_column(
        "runs",
        sa.Column(
            "current_worker_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "runs",
        sa.Column("current_worker_name", sa.String(255), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("fencing_token", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "runs",
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "runs",
        sa.Column("lease_acquired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("last_release_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("last_dispatch_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_runs_available_at", "runs", ["available_at"])
    op.create_index("ix_runs_current_worker_id", "runs", ["current_worker_id"])
    op.create_index("ix_runs_lease_expires_at", "runs", ["lease_expires_at"])
    op.create_index(
        "ix_runs_dispatch_eligible",
        "runs",
        ["dispatch_status", "available_at", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_runs_dispatch_eligible", table_name="runs")
    op.drop_index("ix_runs_lease_expires_at", table_name="runs")
    op.drop_index("ix_runs_current_worker_id", table_name="runs")
    op.drop_index("ix_runs_available_at", table_name="runs")
    op.drop_column("runs", "last_dispatch_error")
    op.drop_column("runs", "last_release_reason")
    op.drop_column("runs", "heartbeat_at")
    op.drop_column("runs", "lease_expires_at")
    op.drop_column("runs", "lease_acquired_at")
    op.drop_column("runs", "attempt")
    op.drop_column("runs", "fencing_token")
    op.drop_column("runs", "current_worker_name")
    op.drop_column("runs", "current_worker_id")
    op.drop_column("runs", "available_at")
