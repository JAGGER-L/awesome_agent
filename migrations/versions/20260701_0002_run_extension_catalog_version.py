"""Add durable extension catalog pin to runs.

Revision ID: 20260701_0002
Revises: 20260628_0001
Create Date: 2026-07-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260701_0002"
down_revision = "20260628_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("extension_catalog_version", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "extension_catalog_version")
