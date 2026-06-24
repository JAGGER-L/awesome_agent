from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from awesome_agent.domain.enums import (
    AgentKind,
    AgentStatus,
    EventType,
    RunMode,
    RunStatus,
    TodoStatus,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class Run(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    goal: str
    mode: RunMode = RunMode.SOLO
    status: RunStatus = RunStatus.CREATED
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
    created_at: datetime = Field(default_factory=utc_now)


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
    event_type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    team_id: UUID | None = None
    agent_id: UUID | None = None
    parent_agent_id: UUID | None = None
    task_id: UUID | None = None
    trace_id: str | None = None
    span_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
