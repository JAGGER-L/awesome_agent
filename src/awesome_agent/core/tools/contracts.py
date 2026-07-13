from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

TOOL_NAME_PATTERN = (
    r"^(?:[a-z][a-z0-9_]*|"
    r"mcp\.[a-z][a-z0-9_-]*\.[a-z][a-z0-9_-]*|"
    r"user\.[a-z][a-z0-9_-]*\.[a-z][a-z0-9_-]*)$"
)


class ToolStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"


class ToolExecutionOrigin(StrEnum):
    AGENT = "agent"
    DIRECT = "direct"


class ToolErrorCode(StrEnum):
    INVALID_ARGUMENTS = "invalid_arguments"
    NOT_FOUND = "not_found"
    WORKSPACE_NOT_TRUSTED = "workspace_not_trusted"
    WORKSPACE_ESCAPE = "workspace_escape"
    PERMISSION_DENIED = "permission_denied"
    CONFLICT = "conflict"
    TIMEOUT = "timeout"
    EXECUTION_FAILED = "execution_failed"
    UNCERTAIN_OUTCOME = "uncertain_outcome"
    MEMORY_DISABLED = "memory_disabled"
    MEMORY_CONFLICT = "memory_conflict"
    MEMORY_REJECTED = "memory_rejected"
    CANCELLED = "cancelled"


class ToolActivityDraft(BaseModel):
    """Terminal audit data with no raw tool arguments or result bodies."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    thread_id: str = Field(min_length=1, max_length=128)
    turn_id: str | None = Field(default=None, max_length=128)
    operation_id: str = Field(min_length=1, max_length=128)
    call_id: str = Field(min_length=1, max_length=128)
    origin: ToolExecutionOrigin
    tool_name: str = Field(min_length=1, max_length=200)
    outcome: Literal["success", "error", "cancelled"]
    input_summary: str = Field(default="", max_length=2_000)
    result_summary: str = Field(default="", max_length=4_000)
    error_code: str | None = Field(default=None, max_length=128)
    duration_ms: int = Field(ge=0)
    change_set_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_origin_turn(self) -> Self:
        if self.origin is ToolExecutionOrigin.AGENT and self.turn_id is None:
            raise ValueError("agent ToolActivity requires turn_id")
        if self.origin is ToolExecutionOrigin.DIRECT and self.turn_id is not None:
            raise ValueError("direct ToolActivity forbids turn_id")
        return self


class ToolActivityWriter(Protocol):
    def finalize(self, activity: ToolActivityDraft) -> None: ...


class ToolSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str = Field(min_length=1, max_length=500)
    input_schema: dict[str, JsonValue]
    capability: str = Field(pattern=r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")
    read_only: bool
    display_metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ToolRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(pattern=TOOL_NAME_PATTERN)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ToolError(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: ToolErrorCode
    message: str = Field(min_length=1, max_length=2_000)
    retryable: bool = False


class ToolPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verb: str = Field(min_length=1, max_length=64)
    target: str | None = Field(default=None, max_length=2_000)
    outcome: str | None = Field(default=None, max_length=128)
    summary: str = Field(default="", max_length=2_000)
    detail: str | None = Field(default=None, max_length=4_000)
    detail_truncated_count: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: str
    tool_name: str
    status: ToolStatus
    content: str = Field(max_length=30_000)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    error: ToolError | None = None
    presentation: ToolPresentation | None = None

    @model_validator(mode="after")
    def error_matches_status(self) -> ToolResult:
        if (self.status is ToolStatus.ERROR) != (self.error is not None):
            raise ValueError("error must be present exactly when status is error")
        return self


class ToolOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    presentation: ToolPresentation | None = None
