from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RepositoryRecord(Base):
    __tablename__ = "repositories"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    root: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(255))
    git_common_dir: Mapped[str] = mapped_column(Text, unique=True)
    default_branch: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ThreadRecord(Base):
    __tablename__ = "threads"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    context_kind: Mapped[str] = mapped_column(String(32), default="workspace")
    context_path: Mapped[str | None] = mapped_column(Text)
    repository_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="SET NULL"),
        index=True,
    )
    default_model: Mapped[str | None] = mapped_column(String(128))
    sandbox_profile: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ThreadMessageRecord(Base):
    __tablename__ = "thread_messages"
    __table_args__ = (
        Index(
            "ix_thread_messages_thread_sequence", "thread_id", "sequence", unique=True
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    thread_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="SET NULL"),
        index=True,
    )
    message_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
    )
    sequence: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RunRecord(Base):
    __tablename__ = "runs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    goal: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    repository_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="RESTRICT"),
        index=True,
    )
    base_commit: Mapped[str | None] = mapped_column(String(64))
    intent: Mapped[str] = mapped_column(String(32))
    execution_kind: Mapped[str] = mapped_column(String(32), index=True)
    parent_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="SET NULL"),
        index=True,
    )
    root_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    child_role: Mapped[str | None] = mapped_column(String(64), index=True)
    runtime_route: Mapped[str | None] = mapped_column(String(128))
    extension_catalog_version: Mapped[str | None] = mapped_column(String(128))
    dispatch_status: Mapped[str] = mapped_column(String(32), index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    current_worker_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), index=True
    )
    current_worker_name: Mapped[str | None] = mapped_column(String(255))
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    lease_acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_release_reason: Mapped[str | None] = mapped_column(Text)
    last_dispatch_error: Mapped[str | None] = mapped_column(Text)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    cancel_requested_by: Mapped[str | None] = mapped_column(String(255))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    result_text: Mapped[str | None] = mapped_column(Text)
    workspace_path: Mapped[str | None] = mapped_column(Text)
    integration_branch: Mapped[str | None] = mapped_column(String(255))
    workspace_state: Mapped[str | None] = mapped_column(String(32))
    workspace_retention_status: Mapped[str] = mapped_column(
        String(32),
        default="retained",
        server_default="retained",
        index=True,
    )
    workspace_cleaned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    workspace_cleanup_reason: Mapped[str | None] = mapped_column(Text)
    graph_thread_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    legacy: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeatRecord(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    worker_name: Mapped[str] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    supported_runtime_routes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), index=True)


class RunBudgetLedgerRecord(Base):
    __tablename__ = "run_budget_ledgers"

    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
    active_seconds: Mapped[int] = mapped_column(Integer, default=0)
    model_call_count: Mapped[int] = mapped_column(Integer, default=0)
    threshold_status: Mapped[str] = mapped_column(String(64), index=True)
    active_window_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ContextCompactionRecord(Base):
    __tablename__ = "context_compactions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    agent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    runtime_route: Mapped[str] = mapped_column(String(128), index=True)
    before_estimated_tokens: Mapped[int] = mapped_column(Integer)
    after_estimated_tokens: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    artifact_refs: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TeamAssignmentRecord(Base):
    __tablename__ = "team_assignments"
    __table_args__ = (
        Index("ix_team_assignments_root_status", "root_run_id", "status"),
        Index("ix_team_assignments_parent_status", "parent_run_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    root_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    parent_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    child_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    role_profile: Mapped[str] = mapped_column(String(128))
    runtime_route: Mapped[str] = mapped_column(String(128))
    goal: Mapped[str] = mapped_column(Text)
    allowed_tools: Mapped[list[str]] = mapped_column(JSONB, default=list)
    deferred_tools: Mapped[list[str]] = mapped_column(JSONB, default=list)
    promoted_tools: Mapped[list[str]] = mapped_column(JSONB, default=list)
    allowed_skills: Mapped[list[str]] = mapped_column(JSONB, default=list)
    can_write: Mapped[bool] = mapped_column(Boolean, default=False)
    can_delegate: Mapped[bool] = mapped_column(Boolean, default=False)
    max_subagents: Mapped[int] = mapped_column(Integer, default=0)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSONB, default=list)
    handoff_context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    retire_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TeamMailboxMessageRecord(Base):
    __tablename__ = "team_mailbox_messages"
    __table_args__ = (
        Index("ix_team_mailbox_root_recipient", "team_root_run_id", "recipient_run_id"),
        Index("ix_team_mailbox_recipient_status", "recipient_run_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    team_root_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    sender_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    sender_agent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    recipient_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    recipient_agent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    route: Mapped[str] = mapped_column(String(64), index=True)
    message_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    subject: Mapped[str] = mapped_column(String(512))
    body_summary: Mapped[str] = mapped_column(Text)
    artifact_refs: Mapped[list[str]] = mapped_column(JSONB, default=list)
    requires_response: Mapped[bool] = mapped_column(Boolean, default=False)
    response_to_message_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("team_mailbox_messages.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TeamChildResultRecord(Base):
    __tablename__ = "team_child_results"

    child_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    assignment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("team_assignments.id", ondelete="CASCADE"),
        index=True,
    )
    parent_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    root_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    summary: Mapped[str] = mapped_column(Text)
    patch_artifact_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("artifacts.id", ondelete="SET NULL"),
        index=True,
    )
    changed_files: Mapped[list[str]] = mapped_column(JSONB, default=list)
    evidence_artifact_refs: Mapped[list[str]] = mapped_column(JSONB, default=list)
    failure_kind: Mapped[str | None] = mapped_column(String(64), index=True)
    patch_aggregated: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IntakeReservationRecord(Base):
    __tablename__ = "intake_reservations"
    __table_args__ = (
        Index(
            "ix_intake_reservations_repository_branch",
            "repository_id",
            "integration_branch",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    repository_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="RESTRICT"),
        index=True,
    )
    base_commit: Mapped[str] = mapped_column(String(64))
    intent: Mapped[str] = mapped_column(String(32))
    workspace_path: Mapped[str] = mapped_column(Text, unique=True)
    integration_branch: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentRecord(Base):
    __tablename__ = "agents"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    parent_agent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(32))
    profile: Mapped[str] = mapped_column(String(128))
    model: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TodoRecord(Base):
    __tablename__ = "todos"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("todos.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), index=True)
    primary_owner_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL")
    )
    collaborator_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSONB, default=list)
    blocker: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RuntimeEventRecord(Base):
    __tablename__ = "runtime_events"
    __table_args__ = (
        Index("ix_runtime_events_run_sequence", "run_id", "sequence", unique=True),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    transition_id: Mapped[str | None] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    team_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    agent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    parent_agent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    task_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArtifactRecord(Base):
    __tablename__ = "artifacts"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(64), index=True)
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ToolInvocationRecord(Base):
    __tablename__ = "tool_invocations"
    __table_args__ = (
        Index(
            "uq_tool_invocations_run_idempotency",
            "run_id",
            "idempotency_key",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(128), index=True)
    tool_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    arguments_hash: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(32))
    path_refs: Mapped[list[str]] = mapped_column(JSONB, default=list)
    preimage_hashes: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    expected_postimage_hashes: Mapped[dict[str, str]] = mapped_column(
        JSONB,
        default=dict,
    )
    result_summary: Mapped[str | None] = mapped_column(Text)
    result_content: Mapped[str | None] = mapped_column(Text)
    result_is_error: Mapped[bool] = mapped_column(Boolean, default=False)
    artifact_refs: Mapped[list[str]] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalRecord(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        Index(
            "uq_approvals_run_tool_call",
            "run_id",
            "tool_call_id",
            unique=True,
        ),
        Index("ix_approvals_status_expires_at", "status", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    tool_invocation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tool_invocations.id", ondelete="CASCADE"),
        index=True,
    )
    tool_call_id: Mapped[str] = mapped_column(String(255))
    tool_name: Mapped[str] = mapped_column(String(128), index=True)
    tool_version: Mapped[str] = mapped_column(String(32))
    canonical_arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    arguments_hash: Mapped[str] = mapped_column(String(64))
    workspace_path: Mapped[str] = mapped_column(Text)
    workspace_fingerprint: Mapped[str] = mapped_column(String(64))
    capabilities: Mapped[list[str]] = mapped_column(JSONB, default=list)
    risk_level: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ValidationReportRecord(Base):
    __tablename__ = "validation_reports"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ValidationGateResultRecord(Base):
    __tablename__ = "validation_gate_results"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    report_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("validation_reports.id", ondelete="CASCADE"),
        index=True,
    )
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    gate_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(255))
    command: Mapped[list[str]] = mapped_column(JSONB, default=list)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    stdout_summary: Mapped[str] = mapped_column(Text, default="")
    stderr_summary: Mapped[str] = mapped_column(Text, default="")
    artifact_refs: Mapped[list[str]] = mapped_column(JSONB, default=list)
    failure_kind: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ObservabilitySpanRecord(Base):
    __tablename__ = "observability_spans"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    span_id: Mapped[str] = mapped_column(String(32), index=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class ObservabilityMetricRecord(Base):
    __tablename__ = "observability_metrics"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), index=True)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ModelCallRecord(Base):
    __tablename__ = "model_calls"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    turn: Mapped[int] = mapped_column(Integer, index=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(64), index=True)
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
