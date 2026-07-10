from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class ToolStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"


class ToolErrorCode(StrEnum):
    INVALID_ARGUMENTS = "invalid_arguments"
    NOT_FOUND = "not_found"
    WORKSPACE_ESCAPE = "workspace_escape"
    PERMISSION_DENIED = "permission_denied"
    CONFLICT = "conflict"
    TIMEOUT = "timeout"
    EXECUTION_FAILED = "execution_failed"


class ToolSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1, max_length=500)
    input_schema: dict[str, JsonValue]
    read_only: bool


class ToolRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ToolError(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: ToolErrorCode
    message: str = Field(min_length=1, max_length=2_000)
    retryable: bool = False


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: str
    tool_name: str
    status: ToolStatus
    content: str = Field(max_length=30_000)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    error: ToolError | None = None

    @model_validator(mode="after")
    def error_matches_status(self) -> ToolResult:
        if (self.status is ToolStatus.ERROR) != (self.error is not None):
            raise ValueError("error must be present exactly when status is error")
        return self


class ToolOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
