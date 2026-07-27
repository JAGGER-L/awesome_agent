from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    field_validator,
    model_validator,
)

from awesome_agent.config.credentials import (
    CredentialService,
    ProviderCredentialStatuses,
    missing_provider_credential_statuses,
)
from awesome_agent.config.models import CredentialSource, SecretStatus
from awesome_agent.context.workspace_instructions import WorkspaceInstructionDiagnostic
from awesome_agent.conversation.models import Thread, ThreadView
from awesome_agent.core.changes import ChangeDelta
from awesome_agent.core.tools.permissions import PermissionMode
from awesome_agent.modeling.catalog import ModelCatalog, ModelIdentitySnapshot


class ProductErrorCode(StrEnum):
    CONFIGURATION_INVALID = "configuration_invalid"
    WORKSPACE_NOT_TRUSTED = "workspace_not_trusted"
    THREAD_NOT_FOUND = "thread_not_found"
    TURN_NOT_FOUND = "turn_not_found"
    TURN_BUSY = "turn_busy"
    OPERATION_BUSY = "operation_busy"
    MODEL_NOT_CONFIGURED = "model_not_configured"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    INVALID_ARGUMENTS = "invalid_arguments"
    COMMAND_NOT_AVAILABLE = "command_not_available"
    RESULT_TOO_LARGE = "result_too_large"
    CHECKPOINT_MISSING = "checkpoint_missing"
    CHECKPOINT_CORRUPT = "checkpoint_corrupt"
    RECOVERY_REQUIRED = "recovery_required"
    CLIENT_VERSION_INCOMPATIBLE = "client_version_incompatible"
    PROTOCOL_VERSION_INCOMPATIBLE = "protocol_version_incompatible"
    STATE_CREATED_BY_NEWER_VERSION = "state_created_by_newer_version"
    STATE_UNKNOWN = "state_unknown"
    STATE_UNAVAILABLE = "state_unavailable"
    STATE_RESET_BUSY = "state_reset_busy"
    STATE_RESET_FAILED = "state_reset_failed"
    INTERNAL_ERROR = "internal_error"


class ProviderCredentialSetStatus(StrEnum):
    CONFIGURED = "configured"
    DELETED = "deleted"
    INVALID = "invalid"
    CONFIRM_UNVERIFIED = "confirm_unverified"


class ProviderCredentialSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: CredentialService
    action: Literal["add", "replace", "delete"]
    api_key: SecretStr | None = None
    allow_unverified: bool = False

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value()
        if not raw.strip() or "\0" in raw or "\r" in raw or "\n" in raw:
            raise ValueError("Provider credential value is invalid.")
        return value

    @model_validator(mode="after")
    def validate_action_value(self) -> Self:
        if self.action == "delete":
            if self.api_key is not None or self.allow_unverified:
                raise ValueError("Delete does not accept credential content.")
            return self
        if self.api_key is None:
            raise ValueError("Credential content is required.")
        return self


class ProviderCredentialSetResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: CredentialService
    status: ProviderCredentialSetStatus
    source: CredentialSource | None
    code: str = Field(min_length=1, max_length=128)


class ProductError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ProductErrorCode
    message: str = Field(min_length=1, max_length=2_000)
    retryable: bool = False
    data: dict[str, JsonValue] = Field(default_factory=dict)


class ApplicationResult[T](BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    value: T | None = None
    error: ProductError | None = None

    @classmethod
    def success(cls, value: T) -> Self:
        return cls(ok=True, value=value)

    @classmethod
    def failure(cls, error: ProductError) -> Self:
        return cls(ok=False, error=error)

    @model_validator(mode="after")
    def validate_branch(self) -> Self:
        if self.ok and self.value is not None and self.error is None:
            return self
        if not self.ok and self.value is None and self.error is not None:
            return self
        raise ValueError("ApplicationResult must contain exactly one matching branch.")


class ShutdownResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stopped: Literal[True] = True


class InitializeStatus(StrEnum):
    READY = "ready"
    TRUST_REQUIRED = "trust_required"
    STATE_RESET_REQUIRED = "state_reset_required"


class InitializeParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal[4]
    client_name: Literal["awesome"]
    client_version: str = Field(min_length=1, max_length=64)


class WorkspacePresentation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    display_path: str = Field(min_length=1, max_length=4_096)
    branch: str | None = Field(default=None, min_length=1, max_length=255)


class InitializeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_version: str = Field(min_length=1, max_length=64)
    protocol_version: Literal[4]
    status: InitializeStatus
    session_id: str = Field(min_length=1, max_length=128)
    interaction_id: str | None = Field(default=None, max_length=128)
    workspace: WorkspacePresentation
    capabilities: tuple[str, ...] = ()


class InteractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    status: str = Field(min_length=1, max_length=128)
    error: ProductError | None = None


class CancelResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(min_length=1, max_length=128)
    cancelled: bool


class ApplicationState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    initialized: bool
    session_id: str = Field(min_length=1, max_length=128)
    workspace_key: str = Field(min_length=1, max_length=128)
    workspace: WorkspacePresentation
    workspace_trusted: bool
    current_thread_id: str | None = Field(default=None, max_length=128)
    model_catalog: ModelCatalog
    model_identity: ModelIdentitySnapshot | None = None
    thinking_enabled: bool = True
    skill_mode: str = Field(default="auto", min_length=1, max_length=64)
    active_operation_id: str | None = Field(default=None, max_length=128)
    pending_interaction_id: str | None = Field(default=None, max_length=128)
    permission_mode: PermissionMode = PermissionMode.REQUEST_APPROVAL
    configuration_valid: bool
    secret_status: SecretStatus
    provider_credentials: ProviderCredentialStatuses = Field(
        default_factory=missing_provider_credential_statuses
    )
    memory_status: dict[str, JsonValue] = Field(default_factory=dict)
    mcp_status: tuple[dict[str, JsonValue], ...] = ()
    usage: dict[str, int | float] = Field(default_factory=dict)
    configuration_diagnostics: tuple[str, ...] = ()
    workspace_instruction_diagnostic: WorkspaceInstructionDiagnostic | None = None


class ThreadListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cursor: str | None = Field(default=None, min_length=1, max_length=1_024)
    limit: int = Field(default=50, ge=1, le=200)


class ThreadSearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    query: str = Field(min_length=1, max_length=200)
    cursor: str | None = Field(default=None, min_length=1, max_length=1_024)
    limit: int = Field(default=50, ge=1, le=50)

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ThreadReadQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    thread_id: str = Field(min_length=1, max_length=128)
    before_sequence: int | None = Field(default=None, ge=1)
    limit: int = Field(default=100, ge=1, le=500)


class ThreadListResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    threads: tuple[Thread, ...] = ()
    has_more: bool = False
    next_cursor: str | None = Field(default=None, min_length=1, max_length=1_024)


class ChangeSetSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    change_set_id: str = Field(min_length=1, max_length=128)
    turn_id: str | None = Field(default=None, max_length=128)
    operation_id: str | None = Field(default=None, max_length=128)
    lifecycle: str = Field(min_length=1, max_length=64)
    changes: tuple[ChangeDelta, ...] = Field(default=(), max_length=1_000)
    created_at: datetime
    sealed_at: datetime | None = None


class StatusSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    workspace_path: str = Field(min_length=1, max_length=4_096)
    thread_title: str = Field(min_length=1, max_length=500)
    thread_id: str = Field(min_length=1, max_length=128)
    thread_display_id: str = Field(min_length=1, max_length=128)
    model_identity: ModelIdentitySnapshot
    model_status: Literal["configured", "not_configured"]
    thinking_enabled: bool
    skill_mode: str = Field(min_length=1, max_length=64)
    local_memory_enabled: bool
    mem0_enabled: bool
    mcp_ready: int = Field(ge=0)
    mcp_degraded: int = Field(ge=0)
    operation_status: Literal["idle", "active"]
    operation_id: str | None = Field(default=None, max_length=128)
    configuration_valid: bool
    configuration_diagnostic_count: int = Field(ge=0)
    permission_mode: PermissionMode = PermissionMode.REQUEST_APPROVAL
    credential_source: CredentialSource | None = None
    credential_source_available: bool = False
    context_used_tokens: int = Field(default=0, ge=0)
    context_budget_tokens: int = Field(default=262_144, ge=1)
    changed_file_count: int = Field(default=0, ge=0)


def thread_display_id(
    thread_id: str,
    *,
    candidate_ids: Iterable[str] = (),
) -> str:
    matched = re.fullmatch(r"thread_([a-f0-9]{8,})", thread_id)
    if matched is None:
        return thread_id
    suffix = matched.group(1)
    candidates = set(candidate_ids)
    candidates.add(thread_id)
    for length in range(8, len(suffix) + 1):
        prefix = f"thread_{suffix[:length]}"
        if sum(candidate.startswith(prefix) for candidate in candidates) == 1:
            return prefix
    return thread_id


class ThreadReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    view: ThreadView
    change_sets: tuple[ChangeSetSummary, ...] = ()
    has_more: bool = False
    next_before_sequence: int | None = Field(default=None, ge=1)


class OperationAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(min_length=1, max_length=128)
    thread_id: str | None = Field(default=None, max_length=128)
    turn_id: str | None = Field(default=None, max_length=128)
    client_message_id: str | None = Field(
        default=None,
        pattern=r"^client_[A-Za-z0-9_-]+$",
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_turn_identity(self) -> OperationAccepted:
        if self.turn_id is not None and (
            self.thread_id is None or self.client_message_id is None
        ):
            raise ValueError("Turn acceptance requires thread_id and client_message_id")
        if self.client_message_id is not None and self.turn_id is None:
            raise ValueError("client_message_id requires turn_id")
        return self
