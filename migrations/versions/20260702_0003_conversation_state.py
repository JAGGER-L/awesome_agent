"""Add durable conversation thread and message state.

Revision ID: 20260702_0003
Revises: 20260701_0002
Create Date: 2026-07-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260702_0003"
down_revision = "20260701_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("context_kind", sa.String(length=32), nullable=False),
        sa.Column("context_path", sa.Text(), nullable=True),
        sa.Column("default_model", sa.String(length=128), nullable=True),
        sa.Column("sandbox_profile", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_threads_title", "threads", ["title"])
    op.create_index("ix_threads_updated_at", "threads", ["updated_at"])

    op.create_table(
        "thread_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_thread_messages_thread_id", "thread_messages", ["thread_id"])
    op.create_index("ix_thread_messages_role", "thread_messages", ["role"])
    op.create_index("ix_thread_messages_kind", "thread_messages", ["kind"])
    op.create_index("ix_thread_messages_run_id", "thread_messages", ["run_id"])
    op.create_index("ix_thread_messages_created_at", "thread_messages", ["created_at"])
    op.create_index(
        "ix_thread_messages_thread_sequence",
        "thread_messages",
        ["thread_id", "sequence"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_thread_messages_thread_sequence", table_name="thread_messages")
    op.drop_index("ix_thread_messages_created_at", table_name="thread_messages")
    op.drop_index("ix_thread_messages_run_id", table_name="thread_messages")
    op.drop_index("ix_thread_messages_kind", table_name="thread_messages")
    op.drop_index("ix_thread_messages_role", table_name="thread_messages")
    op.drop_index("ix_thread_messages_thread_id", table_name="thread_messages")
    op.drop_table("thread_messages")
    op.drop_index("ix_threads_updated_at", table_name="threads")
    op.drop_index("ix_threads_title", table_name="threads")
    op.drop_table("threads")
