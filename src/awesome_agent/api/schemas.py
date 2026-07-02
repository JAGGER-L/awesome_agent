from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.conversation.models import ThreadMessageKind, ThreadMessageRole
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


class CreateThreadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="Untitled thread", min_length=1, max_length=200)
    context_kind: str = Field(default="workspace", min_length=1, max_length=32)
    context_path: str | None = None
    repository_id: UUID | None = None
    default_model: str | None = Field(default=None, max_length=128)
    sandbox_profile: str | None = Field(default=None, max_length=64)


class CreateThreadMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ThreadMessageRole = ThreadMessageRole.USER
    content: str = Field(min_length=1)
    kind: ThreadMessageKind = ThreadMessageKind.MESSAGE
    run_id: UUID | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class CreateConversationTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)
    model: str | None = Field(default=None, max_length=128)


class CreateThreadRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1)
    intent: RunIntent = RunIntent.MODIFYING
    mode: RunMode = RunMode.SOLO
    repository_id: UUID | None = None
    repository_path: str | None = Field(default=None, min_length=1)


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
    runtime_route: str
    before_estimated_tokens: int
    after_estimated_tokens: int
    summary: str
    artifact_refs: list[UUID]
    created_at: datetime


class ModelProfileResponse(BaseModel):
    role: str
    name: str
    provider: str
    configured: bool
    api_key_env: str
    base_url: str | None = None


class SurfaceToolItemResponse(BaseModel):
    name: str
    source: str
    category: str
    risk_level: str
    required_capabilities: list[str] = Field(default_factory=list)
    enabled: bool = True
    health: str = "unknown"
    description: str = ""


class SurfaceToolsResponse(BaseModel):
    builtin: list[SurfaceToolItemResponse] = Field(default_factory=list)
    sandbox: list[SurfaceToolItemResponse] = Field(default_factory=list)
    mcp: list[SurfaceToolItemResponse] = Field(default_factory=list)
    extension: list[SurfaceToolItemResponse] = Field(default_factory=list)


class ExtensionSkillsResponse(BaseModel):
    configured: bool
    items: list[dict[str, Any]] = Field(default_factory=list)


class McpServersResponse(BaseModel):
    configured: bool
    items: list[dict[str, Any]] = Field(default_factory=list)


class MemoryStatusResponse(BaseModel):
    enabled: bool
    provider: str
    configured: bool
    source: str
    hint: str | None = None


class ThreadUploadsResponse(BaseModel):
    thread_id: UUID
    configured: bool = False
    items: list[dict[str, Any]] = Field(default_factory=list)


class ThreadArtifactsResponse(BaseModel):
    thread_id: UUID
    items: list[dict[str, Any]] = Field(default_factory=list)


class ThreadUsageResponse(BaseModel):
    thread_id: UUID
    run_id: UUID | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    active_seconds: int = 0
    model_call_count: int = 0
    threshold_status: str = "not_configured"


class ConfigStatusResponse(BaseModel):
    api_host: str
    local_config_path: str
    artifact_root: str
    workspace_root: str | None
    sandbox_backend: str
    local_cli_sandbox_backend: str
    observability_enabled: bool
    deepseek_api_key_env: str = "AWESOME_AGENT_DEEPSEEK_API_KEY"
    deepseek_api_key_configured: bool
    mem0_api_key_env: str = "AWESOME_AGENT_MEM0_API_KEY"
    mem0_api_key_configured: bool
