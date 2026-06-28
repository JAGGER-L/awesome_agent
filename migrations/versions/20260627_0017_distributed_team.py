"""Add distributed team lineage records.

Revision ID: 20260627_0017
Revises: 20260627_0016
Create Date: 2026-06-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260627_0017"
down_revision: str | None = "20260627_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("parent_run_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "runs",
        sa.Column("root_run_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "runs",
        sa.Column(
            "depth",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column("runs", sa.Column("child_role", sa.String(length=64)))
    op.create_foreign_key(
        "fk_runs_parent_run_id_runs",
        "runs",
        "runs",
        ["parent_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_runs_parent_run_id", "runs", ["parent_run_id"])
    op.create_index("ix_runs_root_run_id", "runs", ["root_run_id"])
    op.create_index("ix_runs_child_role", "runs", ["child_role"])

    op.create_table(
        "team_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("root_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("child_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("role_profile", sa.String(length=128), nullable=False),
        sa.Column("graph_name", sa.String(length=128), nullable=False),
        sa.Column("graph_version", sa.Integer(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("allowed_tools", postgresql.JSONB(), nullable=False),
        sa.Column("allowed_skills", postgresql.JSONB(), nullable=False),
        sa.Column("can_write", sa.Boolean(), nullable=False),
        sa.Column("can_delegate", sa.Boolean(), nullable=False),
        sa.Column("max_subagents", sa.Integer(), nullable=False),
        sa.Column("acceptance_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("handoff_context", postgresql.JSONB(), nullable=False),
        sa.Column("retire_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["child_run_id"],
            ["runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("child_run_id"),
    )
    op.create_index(
        "ix_team_assignments_root_status",
        "team_assignments",
        ["root_run_id", "status"],
    )
    op.create_index(
        "ix_team_assignments_parent_status",
        "team_assignments",
        ["parent_run_id", "status"],
    )
    op.create_index(
        "ix_team_assignments_root_run_id", "team_assignments", ["root_run_id"]
    )
    op.create_index(
        "ix_team_assignments_parent_run_id", "team_assignments", ["parent_run_id"]
    )
    op.create_index(
        "ix_team_assignments_child_run_id", "team_assignments", ["child_run_id"]
    )
    op.create_index("ix_team_assignments_kind", "team_assignments", ["kind"])
    op.create_index("ix_team_assignments_status", "team_assignments", ["status"])
    op.create_index(
        "ix_team_assignments_created_at", "team_assignments", ["created_at"]
    )

    op.create_table(
        "team_mailbox_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_root_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sender_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sender_agent_id", postgresql.UUID(as_uuid=True)),
        sa.Column("recipient_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_agent_id", postgresql.UUID(as_uuid=True)),
        sa.Column("route", sa.String(length=64), nullable=False),
        sa.Column("message_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("body_summary", sa.Text(), nullable=False),
        sa.Column("artifact_refs", postgresql.JSONB(), nullable=False),
        sa.Column("requires_response", sa.Boolean(), nullable=False),
        sa.Column("response_to_message_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("responded_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["response_to_message_id"],
            ["team_mailbox_messages.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_team_mailbox_root_recipient",
        "team_mailbox_messages",
        ["team_root_run_id", "recipient_run_id"],
    )
    op.create_index(
        "ix_team_mailbox_recipient_status",
        "team_mailbox_messages",
        ["recipient_run_id", "status"],
    )
    op.create_index(
        "ix_team_mailbox_messages_team_root_run_id",
        "team_mailbox_messages",
        ["team_root_run_id"],
    )
    op.create_index(
        "ix_team_mailbox_messages_sender_run_id",
        "team_mailbox_messages",
        ["sender_run_id"],
    )
    op.create_index(
        "ix_team_mailbox_messages_recipient_run_id",
        "team_mailbox_messages",
        ["recipient_run_id"],
    )
    op.create_index(
        "ix_team_mailbox_messages_route", "team_mailbox_messages", ["route"]
    )
    op.create_index(
        "ix_team_mailbox_messages_message_type",
        "team_mailbox_messages",
        ["message_type"],
    )
    op.create_index(
        "ix_team_mailbox_messages_status", "team_mailbox_messages", ["status"]
    )
    op.create_index(
        "ix_team_mailbox_messages_created_at",
        "team_mailbox_messages",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_team_mailbox_messages_created_at", table_name="team_mailbox_messages"
    )
    op.drop_index("ix_team_mailbox_messages_status", table_name="team_mailbox_messages")
    op.drop_index(
        "ix_team_mailbox_messages_message_type", table_name="team_mailbox_messages"
    )
    op.drop_index("ix_team_mailbox_messages_route", table_name="team_mailbox_messages")
    op.drop_index(
        "ix_team_mailbox_messages_recipient_run_id",
        table_name="team_mailbox_messages",
    )
    op.drop_index(
        "ix_team_mailbox_messages_sender_run_id", table_name="team_mailbox_messages"
    )
    op.drop_index(
        "ix_team_mailbox_messages_team_root_run_id",
        table_name="team_mailbox_messages",
    )
    op.drop_index(
        "ix_team_mailbox_recipient_status", table_name="team_mailbox_messages"
    )
    op.drop_index("ix_team_mailbox_root_recipient", table_name="team_mailbox_messages")
    op.drop_table("team_mailbox_messages")
    op.drop_index("ix_team_assignments_created_at", table_name="team_assignments")
    op.drop_index("ix_team_assignments_status", table_name="team_assignments")
    op.drop_index("ix_team_assignments_kind", table_name="team_assignments")
    op.drop_index("ix_team_assignments_child_run_id", table_name="team_assignments")
    op.drop_index("ix_team_assignments_parent_run_id", table_name="team_assignments")
    op.drop_index("ix_team_assignments_root_run_id", table_name="team_assignments")
    op.drop_index("ix_team_assignments_parent_status", table_name="team_assignments")
    op.drop_index("ix_team_assignments_root_status", table_name="team_assignments")
    op.drop_table("team_assignments")
    op.drop_index("ix_runs_child_role", table_name="runs")
    op.drop_index("ix_runs_root_run_id", table_name="runs")
    op.drop_index("ix_runs_parent_run_id", table_name="runs")
    op.drop_constraint("fk_runs_parent_run_id_runs", "runs", type_="foreignkey")
    op.drop_column("runs", "child_role")
    op.drop_column("runs", "depth")
    op.drop_column("runs", "root_run_id")
    op.drop_column("runs", "parent_run_id")
