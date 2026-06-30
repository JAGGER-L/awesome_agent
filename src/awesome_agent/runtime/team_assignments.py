from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from awesome_agent.domain.models import Run, utc_now


class TeamAssignmentKind(StrEnum):
    TEAMMATE = "teammate"
    VERIFIER = "verifier"
    SUBAGENT = "subagent"


class TeamAssignmentStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    RETIRED = "retired"
    CANCELLED = "cancelled"


class TeamAssignment(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    root_run_id: UUID
    parent_run_id: UUID
    child_run_id: UUID
    kind: TeamAssignmentKind
    status: TeamAssignmentStatus = TeamAssignmentStatus.ACTIVE
    role_profile: str = Field(max_length=128)
    runtime_route: str = Field(max_length=128)
    goal: str
    allowed_tools: list[str] = Field(default_factory=list)
    deferred_tools: list[str] = Field(default_factory=list)
    promoted_tools: list[str] = Field(default_factory=list)
    allowed_skills: list[str] = Field(default_factory=list)
    can_write: bool = False
    can_delegate: bool = False
    max_subagents: int = Field(default=0, ge=0, le=3)
    acceptance_criteria: list[str] = Field(default_factory=list)
    handoff_context: dict[str, Any] = Field(default_factory=dict)
    retire_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TeamChildResult(BaseModel):
    assignment_id: UUID
    child_run_id: UUID
    parent_run_id: UUID
    root_run_id: UUID
    status: Literal["completed", "failed", "cancelled", "recovery_required"]
    summary: str
    patch_artifact_id: UUID | None = None
    changed_files: list[str] = Field(default_factory=list)
    evidence_artifact_refs: list[UUID] = Field(default_factory=list)
    failure_kind: str | None = None
    patch_aggregated: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


def validate_child_depth(parent: Run, child: Run) -> bool:
    if child.depth > 2:
        raise ValueError("maximum team Run depth is 2")
    if child.depth != parent.depth + 1:
        raise ValueError("child Run depth must be exactly parent depth + 1")
    if child.parent_run_id != parent.id:
        raise ValueError("child Run parent_run_id must reference parent Run")
    expected_root = parent.root_run_id or parent.id
    if child.root_run_id != expected_root:
        raise ValueError("child Run root_run_id must match parent root")
    if parent.depth == 2:
        raise ValueError("subagent Runs cannot create child Runs")
    return True


def validate_assignment_graph(assignment: TeamAssignment) -> bool:
    if (
        assignment.kind
        in {
            TeamAssignmentKind.TEAMMATE,
            TeamAssignmentKind.SUBAGENT,
        }
        and assignment.runtime_route != "team-role"
    ):
        raise ValueError("teammate and subagent assignments use team-role")
    if assignment.kind is TeamAssignmentKind.VERIFIER:
        if assignment.runtime_route != "team-verifier":
            raise ValueError("verifier assignments use team-verifier")
        if assignment.can_delegate:
            raise ValueError("verifier assignments cannot delegate")
    if assignment.kind is TeamAssignmentKind.SUBAGENT and (
        assignment.can_delegate or assignment.max_subagents
    ):
        raise ValueError("subagent assignments cannot delegate")
    if assignment.can_delegate and assignment.max_subagents < 1:
        raise ValueError("delegating assignments require at least one subagent slot")
    return True


def effective_assignment_tools(assignment: TeamAssignment) -> list[str]:
    from awesome_agent.runtime.capabilities import CapabilityResolver

    return list(CapabilityResolver().resolve_team_assignment(assignment).tool_names)
