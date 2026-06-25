"""Add repository-aware Run intake persistence.

Revision ID: 20260625_0003
Revises: 20260625_0002
Create Date: 2026-06-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260625_0003"
down_revision: str | None = "20260625_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("root", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("git_common_dir", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.String(length=255), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("git_common_dir"),
    )
    op.create_index("ix_repositories_enabled", "repositories", ["enabled"])

    op.add_column(
        "runs",
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("runs", sa.Column("base_commit", sa.String(64), nullable=True))
    op.add_column(
        "runs",
        sa.Column(
            "intent",
            sa.String(32),
            nullable=False,
            server_default="modifying",
        ),
    )
    op.add_column(
        "runs",
        sa.Column(
            "dispatch_status",
            sa.String(32),
            nullable=False,
            server_default="terminal",
        ),
    )
    op.add_column("runs", sa.Column("workspace_path", sa.Text(), nullable=True))
    op.add_column(
        "runs",
        sa.Column("integration_branch", sa.String(255), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("workspace_state", sa.String(32), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("graph_thread_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column(
            "legacy",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_foreign_key(
        "fk_runs_repository_id",
        "runs",
        "repositories",
        ["repository_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_runs_repository_id", "runs", ["repository_id"])
    op.create_index("ix_runs_dispatch_status", "runs", ["dispatch_status"])
    op.create_unique_constraint(
        "uq_runs_graph_thread_id",
        "runs",
        ["graph_thread_id"],
    )
    op.execute(
        """
        UPDATE runs
        SET legacy = true,
            dispatch_status = 'terminal',
            status = CASE
                WHEN status IN ('created', 'running', 'paused')
                    THEN 'recovery_required'
                ELSE status
            END
        """
    )

    op.create_table(
        "intake_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("base_commit", sa.String(64), nullable=False),
        sa.Column("intent", sa.String(32), nullable=False),
        sa.Column("workspace_path", sa.Text(), nullable=False),
        sa.Column("integration_branch", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
        sa.UniqueConstraint("workspace_path"),
    )
    op.create_index(
        "ix_intake_reservations_repository_id",
        "intake_reservations",
        ["repository_id"],
    )
    op.create_index(
        "ix_intake_reservations_status",
        "intake_reservations",
        ["status"],
    )
    op.create_index(
        "ix_intake_reservations_repository_branch",
        "intake_reservations",
        ["repository_id", "integration_branch"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("intake_reservations")
    op.drop_constraint("uq_runs_graph_thread_id", "runs", type_="unique")
    op.drop_index("ix_runs_dispatch_status", table_name="runs")
    op.drop_index("ix_runs_repository_id", table_name="runs")
    op.drop_constraint("fk_runs_repository_id", "runs", type_="foreignkey")
    op.execute("UPDATE runs SET status = 'created' WHERE status = 'recovery_required'")
    op.drop_column("runs", "legacy")
    op.drop_column("runs", "graph_thread_id")
    op.drop_column("runs", "workspace_state")
    op.drop_column("runs", "integration_branch")
    op.drop_column("runs", "workspace_path")
    op.drop_column("runs", "dispatch_status")
    op.drop_column("runs", "intent")
    op.drop_column("runs", "base_commit")
    op.drop_column("runs", "repository_id")
    op.drop_table("repositories")
