"""conversation runs

Revision ID: 20260703_0005
Revises: 20260702_0004
Create Date: 2026-07-03
"""

import sqlalchemy as sa
from alembic import op

revision = "20260703_0005"
down_revision = "20260702_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("working_directory", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "working_directory")
