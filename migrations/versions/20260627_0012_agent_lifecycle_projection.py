"""Add agent lifecycle projection fields.

Revision ID: 20260627_0012
Revises: 20260626_0011
Create Date: 2026-06-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260627_0012"
down_revision: str | None = "20260626_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "revision",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )
    op.add_column(
        "agents",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )
    op.alter_column("agents", "revision", server_default=None)
    op.alter_column("agents", "updated_at", server_default=None)


def downgrade() -> None:
    op.drop_column("agents", "updated_at")
    op.drop_column("agents", "revision")
