from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from awesome_agent.domain.enums import (
    AgentKind,
    AgentStatus,
    DispatchStatus,
    EventType,
    ExecutionKind,
    IntakeReservationStatus,
    RunIntent,
    RunMode,
    RunStatus,
    TodoStatus,
    WorkspaceRetentionStatus,
    WorkspaceState,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class Run(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    goal: str
    mode: RunMode = RunMode.SOLO
    status: RunStatus = RunStatus.CREATED
    repository_id: UUID | None = None
    base_commit: str | None = None
    intent: RunIntent = RunIntent.MODIFYING
    execution_kind: ExecutionKind = ExecutionKind.CODING
    parent_run_id: UUID | None = None
    root_run_id: UUID | None = None
    depth: int = Field(default=0, ge=0, le=2)
    child_role: str | None = Field(default=None, max_length=64)
    runtime_route: str | None = None
    dispatch_status: DispatchStatus = DispatchStatus.TERMINAL
    available_at: datetime = Field(default_factory=utc_now)
    current_worker_id: UUID | None = None
    current_worker_name: str | None = None
    fencing_token: int = Field(default=0, ge=0)
    attempt: int = Field(default=0, ge=0)
    lease_acquired_at: datetime | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    last_release_reason: str | None = None
    last_dispatch_error: str | None = None
    cancel_requested_at: datetime | None = None
    cancel_requested_by: str | None = Field(default=None, max_length=255)
    cancel_reason: str | None = None
    result_text: str | None = Field(default=None, max_length=32768)
    workspace_path: Path | None = None
    integration_branch: str | None = None
    workspace_state: WorkspaceState | None = None
    workspace_retention_status: WorkspaceRetentionStatus = (
        WorkspaceRetentionStatus.RETAINED
    )
    workspace_cleaned_at: datetime | None = None
    workspace_cleanup_reason: str | None = None
    graph_thread_id: str | None = None
    legacy: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Repository(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    root: Path
    display_name: str
    git_common_dir: Path
    default_branch: str | None = None
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)


class RunWorkspace(BaseModel):
    repository_id: UUID
    base_commit: str
    intent: RunIntent
    dispatch_status: DispatchStatus = DispatchStatus.QUEUED
    workspace_path: Path
    integration_branch: str
    workspace_state: WorkspaceState = WorkspaceState.READY
    graph_thread_id: str


class RunLease(BaseModel):
    run_id: UUID
    worker_id: UUID
    worker_name: str
    fencing_token: int = Field(ge=1)
    attempt: int = Field(ge=1)
    lease_acquired_at: datetime
    lease_expires_at: datetime
    heartbeat_at: datetime


class IntakeReservation(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    repository_id: UUID
    base_commit: str
    intent: RunIntent
    workspace_path: Path
    integration_branch: str
    status: IntakeReservationStatus = IntakeReservationStatus.PREPARING
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Agent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    parent_agent_id: UUID | None = None
    kind: AgentKind
    profile: str
    model: str
    status: AgentStatus = AgentStatus.CREATED
    revision: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TodoItem(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    parent_id: UUID | None = None
    title: str
    description: str = ""
    status: TodoStatus = TodoStatus.TODO
    primary_owner_id: UUID | None = None
    collaborator_ids: list[UUID] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    blocker: str | None = None
    revision: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RuntimeEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    sequence: int = Field(ge=1)
    transition_id: str | None = Field(default=None, max_length=255)
    event_type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    team_id: UUID | None = None
    agent_id: UUID | None = None
    parent_agent_id: UUID | None = None
    task_id: UUID | None = None
    trace_id: str | None = None
    span_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
