"""Add repository context to conversation threads.

Revision ID: 20260702_0004
Revises: 20260702_0003
Create Date: 2026-07-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260702_0004"
down_revision = "20260702_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "threads",
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_threads_repository_id_repositories",
        "threads",
        "repositories",
        ["repository_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_threads_repository_id", "threads", ["repository_id"])


def downgrade() -> None:
    op.drop_index("ix_threads_repository_id", table_name="threads")
    op.drop_constraint(
        "fk_threads_repository_id_repositories",
        "threads",
        type_="foreignkey",
    )
    op.drop_column("threads", "repository_id")
