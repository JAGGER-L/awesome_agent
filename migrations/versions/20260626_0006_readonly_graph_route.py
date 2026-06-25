"""Route read-only coding runs and add durable result identity.

Revision ID: 20260626_0006
Revises: 20260625_0005
Create Date: 2026-06-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260626_0006"
down_revision: str | None = "20260625_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("result_text", sa.Text(), nullable=True))
    op.add_column(
        "runtime_events",
        sa.Column("transition_id", sa.String(255), nullable=True),
    )
    op.create_index(
        "uq_runtime_events_run_transition",
        "runtime_events",
        ["run_id", "transition_id"],
        unique=True,
        postgresql_where=sa.text("transition_id IS NOT NULL"),
    )
    op.execute(
        """
        UPDATE runs
        SET graph_name = 'solo-readonly', graph_version = 1
        WHERE execution_kind = 'coding'
          AND intent = 'read_only'
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
          AND intent = 'read_only'
          AND graph_name = 'solo-readonly'
          AND graph_version = 1
        """
    )
    op.drop_index(
        "uq_runtime_events_run_transition",
        table_name="runtime_events",
    )
    op.drop_column("runtime_events", "transition_id")
    op.drop_column("runs", "result_text")
