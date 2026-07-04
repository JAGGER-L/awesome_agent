from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.conversation.models import ThreadMessageKind, ThreadMessageRole


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
    local_memory_enabled: bool | None = None
    provider_memory: str | None = Field(default=None, max_length=64)


class UpdateThreadSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_model: str | None = Field(default=None, max_length=128)
    thinking_mode: str | None = Field(default=None, max_length=32)
    local_memory_enabled: bool | None = None
    provider_memory: str | None = Field(default=None, max_length=64)


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
    thinking_mode: str | None = Field(default=None, max_length=32)
    memory: dict[str, object] = Field(default_factory=dict)
    skill_ids: list[str] = Field(default_factory=list)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=5)


class ContinueConversationTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_run_id: UUID | None = None
    after_sequence: int = Field(default=0, ge=0)


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: str
    hint: str | None = None
    request_id: str
    trace_id: str | None = None
    recoverable: bool = False


class PaginatedResponse[T](BaseModel):
    items: list[T] = Field(default_factory=list)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)
    has_more: bool = False


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


class ModelItemResponse(BaseModel):
    id: str
    display_name: str
    provider_id: str
    capabilities: list[str] = Field(default_factory=list)
    recommended_for: list[str] = Field(default_factory=list)
    selected: bool = False


class ModelProviderResponse(BaseModel):
    id: str
    display_name: str
    configured: bool
    credential_env: str
    api_key_present: bool
    models: list[ModelItemResponse] = Field(default_factory=list)


class CurrentModelResponse(BaseModel):
    provider_id: str
    model_id: str


class ModelCatalogResponse(BaseModel):
    providers: list[ModelProviderResponse] = Field(default_factory=list)
    current: CurrentModelResponse


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
    builtin_enabled: bool
    provider_enabled: bool
    provider_status: str
    root: str
    files: dict[str, str]
    counts: dict[str, int]
    truncated: dict[str, bool]
    hint: str | None = None


class MemoryEntryResponse(BaseModel):
    id: str
    target: str
    content: str


class MemoryEntriesResponse(BaseModel):
    target: str | None = None
    items: list[MemoryEntryResponse] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    has_more: bool = False


class MemoryDeleteResponse(BaseModel):
    status: str
    memory_id: str
    target: str


class ThreadMemoryStatusResponse(MemoryStatusResponse):
    thread_id: UUID
    provider_thread_scoped: bool = False


class ThreadAttachmentResponse(BaseModel):
    id: UUID
    thread_id: UUID
    scope: str
    status: str
    filename: str
    mime_type: str
    media_type: str
    size: int
    sha256: str
    run_id: UUID | None = None
    message_id: UUID | None = None
    created_at: datetime
    attached_at: datetime | None = None
    deleted_at: datetime | None = None


class ThreadAttachmentsResponse(BaseModel):
    thread_id: UUID
    items: list[ThreadAttachmentResponse] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    has_more: bool = False


class ThreadArtifactsResponse(BaseModel):
    thread_id: UUID
    items: list[dict[str, Any]] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    has_more: bool = False


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
    deepseek_base_url: str
    mem0_api_key_env: str = "AWESOME_AGENT_MEM0_API_KEY"
    mem0_api_key_configured: bool
    project_config_path: str | None = None
    project_config_exists: bool | None = None
    project_env_path: str | None = None
    project_env_exists: bool | None = None
