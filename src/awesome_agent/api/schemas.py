from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.domain.enums import RunIntent, RunMode


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: UUID
    goal: str = Field(min_length=1)
    intent: RunIntent = RunIntent.MODIFYING
    mode: RunMode = RunMode.SOLO


class CreateProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: UUID
    goal: str = Field(default="Verify durable runtime", min_length=1)


class ApprovalDecisionRequest(BaseModel):
    approved: bool


class HealthCheckResponse(BaseModel):
    name: str
    status: str
    severity: str
    detail: str
    remediation: str | None
    metadata: dict[str, object] | None


class ReadinessReportResponse(BaseModel):
    profile: str
    status: str
    generated_at: datetime
    checks: list[HealthCheckResponse]


class DispatchResponse(BaseModel):
    status: str
    available_at: datetime
    worker_id: UUID | None
    worker_name: str | None
    fencing_token: int
    attempt: int
    lease_acquired_at: datetime | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    last_release_reason: str | None
    last_error: str | None


class WorkspaceCleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID | None = None
    older_than: str | None = None
    force: bool = False
    reason: str | None = None


class WorkspaceCandidateResponse(BaseModel):
    run_id: UUID
    repository_id: UUID | None
    workspace_path: str | None
    branch: str | None
    status: str
    retention_status: str
    reason: str
    dirty: bool | None
    can_cleanup: bool


class BudgetLedgerResponse(BaseModel):
    run_id: UUID
    input_tokens: int
    output_tokens: int
    total_tokens: int
    reasoning_tokens: int
    active_seconds: int
    model_call_count: int
    threshold_status: str


class ContextCompactionResponse(BaseModel):
    id: UUID
    run_id: UUID
    agent_id: UUID | None
    graph_name: str
    graph_version: int
    before_estimated_tokens: int
    after_estimated_tokens: int
    summary: str
    artifact_refs: list[UUID]
    created_at: datetime
