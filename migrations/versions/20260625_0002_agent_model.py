"""Persist the selected model for every agent.

Revision ID: 20260625_0002
Revises: 20260624_0001
Create Date: 2026-06-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260625_0002"
down_revision: str | None = "20260624_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "model",
            sa.String(length=128),
            nullable=False,
            server_default="deepseek-v4-flash",
        ),
    )
    op.execute("UPDATE agents SET model = 'deepseek-v4-pro' WHERE kind = 'leader'")


def downgrade() -> None:
    op.drop_column("agents", "model")
