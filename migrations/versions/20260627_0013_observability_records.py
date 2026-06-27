"""Add observability records.

Revision ID: 20260627_0013
Revises: 20260627_0012
Create Date: 2026-06-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260627_0013"
down_revision: str | None = "20260627_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "observability_spans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("span_id", sa.String(length=32), nullable=False),
        sa.Column("parent_span_id", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_observability_spans_run_id",
        "observability_spans",
        ["run_id"],
    )
    op.create_index(
        "ix_observability_spans_trace_id",
        "observability_spans",
        ["trace_id"],
    )
    op.create_index(
        "ix_observability_spans_span_id",
        "observability_spans",
        ["span_id"],
    )
    op.create_index(
        "ix_observability_spans_parent_span_id",
        "observability_spans",
        ["parent_span_id"],
    )
    op.create_index(
        "ix_observability_spans_name",
        "observability_spans",
        ["name"],
    )
    op.create_index(
        "ix_observability_spans_category",
        "observability_spans",
        ["category"],
    )
    op.create_index(
        "ix_observability_spans_status",
        "observability_spans",
        ["status"],
    )
    op.create_index(
        "ix_observability_spans_started_at",
        "observability_spans",
        ["started_at"],
    )

    op.create_table(
        "observability_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column(
            "attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_observability_metrics_run_id",
        "observability_metrics",
        ["run_id"],
    )
    op.create_index(
        "ix_observability_metrics_name",
        "observability_metrics",
        ["name"],
    )
    op.create_index(
        "ix_observability_metrics_created_at",
        "observability_metrics",
        ["created_at"],
    )

    op.create_table(
        "model_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("turn", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("stop_reason", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("span_id", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_calls_run_id", "model_calls", ["run_id"])
    op.create_index("ix_model_calls_agent_id", "model_calls", ["agent_id"])
    op.create_index("ix_model_calls_turn", "model_calls", ["turn"])
    op.create_index("ix_model_calls_provider", "model_calls", ["provider"])
    op.create_index("ix_model_calls_model", "model_calls", ["model"])
    op.create_index("ix_model_calls_status", "model_calls", ["status"])
    op.create_index("ix_model_calls_trace_id", "model_calls", ["trace_id"])
    op.create_index("ix_model_calls_created_at", "model_calls", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_model_calls_created_at", table_name="model_calls")
    op.drop_index("ix_model_calls_trace_id", table_name="model_calls")
    op.drop_index("ix_model_calls_status", table_name="model_calls")
    op.drop_index("ix_model_calls_model", table_name="model_calls")
    op.drop_index("ix_model_calls_provider", table_name="model_calls")
    op.drop_index("ix_model_calls_turn", table_name="model_calls")
    op.drop_index("ix_model_calls_agent_id", table_name="model_calls")
    op.drop_index("ix_model_calls_run_id", table_name="model_calls")
    op.drop_table("model_calls")
    op.drop_index(
        "ix_observability_metrics_created_at",
        table_name="observability_metrics",
    )
    op.drop_index(
        "ix_observability_metrics_name",
        table_name="observability_metrics",
    )
    op.drop_index(
        "ix_observability_metrics_run_id",
        table_name="observability_metrics",
    )
    op.drop_table("observability_metrics")
    op.drop_index(
        "ix_observability_spans_started_at",
        table_name="observability_spans",
    )
    op.drop_index("ix_observability_spans_status", table_name="observability_spans")
    op.drop_index("ix_observability_spans_category", table_name="observability_spans")
    op.drop_index("ix_observability_spans_name", table_name="observability_spans")
    op.drop_index(
        "ix_observability_spans_parent_span_id",
        table_name="observability_spans",
    )
    op.drop_index("ix_observability_spans_span_id", table_name="observability_spans")
    op.drop_index("ix_observability_spans_trace_id", table_name="observability_spans")
    op.drop_index("ix_observability_spans_run_id", table_name="observability_spans")
    op.drop_table("observability_spans")
