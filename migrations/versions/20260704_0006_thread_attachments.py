from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260704_0006"
down_revision = "20260703_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "thread_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["thread_messages.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_thread_attachments_thread_id",
        "thread_attachments",
        ["thread_id"],
    )
    op.create_index(
        "ix_thread_attachments_run_id",
        "thread_attachments",
        ["run_id"],
    )
    op.create_index(
        "ix_thread_attachments_message_id",
        "thread_attachments",
        ["message_id"],
    )
    op.create_index(
        "ix_thread_attachments_status",
        "thread_attachments",
        ["status"],
    )
    op.create_index(
        "ix_thread_attachments_created_at",
        "thread_attachments",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_thread_attachments_created_at", table_name="thread_attachments")
    op.drop_index("ix_thread_attachments_status", table_name="thread_attachments")
    op.drop_index("ix_thread_attachments_message_id", table_name="thread_attachments")
    op.drop_index("ix_thread_attachments_run_id", table_name="thread_attachments")
    op.drop_index("ix_thread_attachments_thread_id", table_name="thread_attachments")
    op.drop_table("thread_attachments")
