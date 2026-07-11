from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from awesome_agent.config.models import SecretStatus
from awesome_agent.conversation.models import Thread, ThreadView


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
    CHECKPOINT_MISSING = "checkpoint_missing"
    CHECKPOINT_CORRUPT = "checkpoint_corrupt"
    RECOVERY_REQUIRED = "recovery_required"
    CLIENT_VERSION_INCOMPATIBLE = "client_version_incompatible"
    PROTOCOL_VERSION_INCOMPATIBLE = "protocol_version_incompatible"
    INTERNAL_ERROR = "internal_error"


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


class InitializeParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal[1]
    client_name: Literal["awesome-tui"]
    client_version: str = Field(min_length=1, max_length=64)


class WorkspacePresentation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    display_path: str = Field(min_length=1, max_length=4_096)
    branch: str | None = Field(default=None, min_length=1, max_length=255)


class InitializeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_version: str = Field(min_length=1, max_length=64)
    protocol_version: Literal[1]
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
    current_model: str | None = Field(default=None, max_length=200)
    thinking_enabled: bool = False
    skill_mode: str = Field(default="auto", min_length=1, max_length=64)
    active_operation_id: str | None = Field(default=None, max_length=128)
    pending_interaction_id: str | None = Field(default=None, max_length=128)
    configuration_valid: bool
    secret_status: SecretStatus
    memory_status: dict[str, JsonValue] = Field(default_factory=dict)
    mcp_status: tuple[dict[str, JsonValue], ...] = ()
    usage: dict[str, int] = Field(default_factory=dict)
    configuration_diagnostics: tuple[str, ...] = ()


class ThreadListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cursor: str | None = Field(default=None, min_length=1, max_length=1_024)
    limit: int = Field(default=50, ge=1, le=200)


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
    changed_paths: tuple[str, ...] = Field(default=(), max_length=1_000)
    reversibility: str = Field(min_length=1, max_length=64)
    created_at: datetime
    sealed_at: datetime | None = None


class StatusSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    workspace_path: str = Field(min_length=1, max_length=4_096)
    thread_title: str = Field(min_length=1, max_length=500)
    thread_id: str = Field(min_length=1, max_length=128)
    thread_display_id: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=200)
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
