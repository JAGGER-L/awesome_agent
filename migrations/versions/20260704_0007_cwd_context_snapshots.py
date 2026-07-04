from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260704_0007"
down_revision = "20260704_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cwd_context_snapshots",
        sa.Column("snapshot_id", sa.String(length=128), primary_key=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("working_directory", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_cwd_context_thread_dir_created",
        "cwd_context_snapshots",
        ["thread_id", "working_directory", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cwd_context_thread_dir_created",
        table_name="cwd_context_snapshots",
    )
    op.drop_table("cwd_context_snapshots")
