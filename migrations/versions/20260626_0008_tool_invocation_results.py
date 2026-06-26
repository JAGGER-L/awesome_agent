"""Add durable tool invocation result fields.

Revision ID: 20260626_0008
Revises: 20260626_0007
Create Date: 2026-06-26 00:08:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260626_0008"
down_revision = "20260626_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tool_invocations",
        sa.Column("result_content", sa.Text(), nullable=True),
    )
    op.add_column(
        "tool_invocations",
        sa.Column(
            "result_is_error",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column("tool_invocations", "result_is_error", server_default=None)


def downgrade() -> None:
    op.drop_column("tool_invocations", "result_is_error")
    op.drop_column("tool_invocations", "result_content")
