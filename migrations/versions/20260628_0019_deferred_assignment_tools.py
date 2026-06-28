"""Add deferred assignment tool fields.

Revision ID: 20260628_0019
Revises: 20260627_0018
Create Date: 2026-06-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260628_0019"
down_revision: str | None = "20260627_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "team_assignments",
        sa.Column(
            "deferred_tools",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "team_assignments",
        sa.Column(
            "promoted_tools",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("team_assignments", "deferred_tools", server_default=None)
    op.alter_column("team_assignments", "promoted_tools", server_default=None)


def downgrade() -> None:
    op.drop_column("team_assignments", "promoted_tools")
    op.drop_column("team_assignments", "deferred_tools")
