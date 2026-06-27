"""Add worker heartbeat registry.

Revision ID: 20260627_0015
Revises: 20260627_0014
Create Date: 2026-06-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260627_0015"
down_revision: str | None = "20260627_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_name", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supported_graphs", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_index(
        "ix_worker_heartbeats_heartbeat_at",
        "worker_heartbeats",
        ["heartbeat_at"],
    )
    op.create_index(
        "ix_worker_heartbeats_status",
        "worker_heartbeats",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_heartbeats_status", table_name="worker_heartbeats")
    op.drop_index("ix_worker_heartbeats_heartbeat_at", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
