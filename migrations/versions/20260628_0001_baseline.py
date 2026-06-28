"""Baseline schema after pre-production reset.

Revision ID: 20260628_0001
Revises:
Create Date: 2026-06-28
"""

from __future__ import annotations

from alembic import op

from awesome_agent.persistence.models import Base

revision = "20260628_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
