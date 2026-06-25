"""Add execution kind and graph identity.

Revision ID: 20260625_0005
Revises: 20260625_0004
Create Date: 2026-06-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260625_0005"
down_revision: str | None = "20260625_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "execution_kind",
            sa.String(32),
            nullable=False,
            server_default="coding",
        ),
    )
    op.add_column(
        "runs",
        sa.Column("graph_name", sa.String(128), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("graph_version", sa.Integer(), nullable=True),
    )
    op.create_index("ix_runs_execution_kind", "runs", ["execution_kind"])
    op.create_check_constraint(
        "ck_runs_graph_identity_complete",
        "runs",
        """
        (graph_name IS NULL AND graph_version IS NULL)
        OR (graph_name IS NOT NULL AND graph_version IS NOT NULL)
        """,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_runs_graph_identity_complete",
        "runs",
        type_="check",
    )
    op.drop_index("ix_runs_execution_kind", table_name="runs")
    op.drop_column("runs", "graph_version")
    op.drop_column("runs", "graph_name")
    op.drop_column("runs", "execution_kind")
