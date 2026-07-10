from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)


class ThreadEntryKind(StrEnum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    DIRECT_COMMAND = "direct_command"


class TurnStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ToolActivityOrigin(StrEnum):
    AGENT = "agent"
    DIRECT = "direct"


class ToolActivityOutcome(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


class UsageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    provider_retries: int = Field(default=0, ge=0)
    compressions: int = Field(default=0, ge=0)
    active_execution_seconds: float = Field(default=0.0, ge=0.0)

    def __add__(self, other: UsageSummary) -> UsageSummary:
        return UsageSummary(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            model_calls=self.model_calls + other.model_calls,
            tool_calls=self.tool_calls + other.tool_calls,
            provider_retries=self.provider_retries + other.provider_retries,
            compressions=self.compressions + other.compressions,
            active_execution_seconds=(
                self.active_execution_seconds + other.active_execution_seconds
            ),
        )


class Thread(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    workspace_key: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=500)
    current_model: str | None = Field(default=None, max_length=200)
    thinking_enabled: bool = False
    skill_mode: str = Field(default="auto", min_length=1, max_length=64)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value


class ThreadEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    kind: ThreadEntryKind
    content: str = Field(max_length=200_000)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime

    @model_validator(mode="after")
    def validate_direct_command_bound(self) -> Self:
        if (
            self.kind is ThreadEntryKind.DIRECT_COMMAND
            and len(self.content) > 30_000
        ):
            raise ValueError("direct_command content exceeds 30000 characters")
        return self


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=128)
    checkpoint_key: str = Field(min_length=1, max_length=128)
    status: TurnStatus
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=200)
    thinking_enabled: bool = False
    skill_mode: str = Field(default="auto", min_length=1, max_length=64)
    user_entry_id: str = Field(min_length=1, max_length=128)
    assistant_entry_id: str | None = Field(default=None, max_length=128)
    usage: UsageSummary = Field(default_factory=UsageSummary)
    termination_reason: str | None = Field(default=None, max_length=128)
    error_code: str | None = Field(default=None, max_length=128)
    context_manifest: tuple[dict[str, JsonValue], ...] = ()
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_identity_and_terminal_shape(self) -> Self:
        if self.checkpoint_key != self.id:
            raise ValueError("checkpoint_key must equal the Turn id")
        if self.status is TurnStatus.IN_PROGRESS:
            if self.completed_at is not None:
                raise ValueError("in-progress Turn cannot have completed_at")
            if self.assistant_entry_id is not None:
                raise ValueError("in-progress Turn cannot have assistant_entry_id")
            if self.error_code is not None:
                raise ValueError("in-progress Turn cannot have error_code")
            return self
        if self.completed_at is None:
            raise ValueError("terminal Turn requires completed_at")
        if self.status is TurnStatus.COMPLETED:
            if self.assistant_entry_id is None:
                raise ValueError("completed Turn requires assistant_entry_id")
            if self.error_code is not None:
                raise ValueError("completed Turn cannot have error_code")
        if self.status is TurnStatus.FAILED and self.error_code is None:
            raise ValueError("failed Turn requires error_code")
        return self


class ThreadSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    thread_id: str = Field(min_length=1, max_length=128)
    content: str = Field(max_length=200_000)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    covered_entry_sequence: int = Field(ge=0)
    covered_turn_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=200)
    updated_at: datetime


class ToolActivity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=128)
    turn_id: str | None = Field(default=None, max_length=128)
    operation_id: str = Field(min_length=1, max_length=128)
    call_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    origin: ToolActivityOrigin
    tool_name: str = Field(min_length=1, max_length=200)
    outcome: ToolActivityOutcome
    input_summary: str = Field(default="", max_length=2_000)
    result_summary: str = Field(default="", max_length=4_000)
    error_code: str | None = Field(default=None, max_length=128)
    duration_ms: int = Field(ge=0)
    change_set_id: str | None = Field(default=None, max_length=128)
    created_at: datetime

    @model_validator(mode="after")
    def validate_origin_turn(self) -> Self:
        if self.origin is ToolActivityOrigin.AGENT and self.turn_id is None:
            raise ValueError("agent ToolActivity requires turn_id")
        if self.origin is ToolActivityOrigin.DIRECT and self.turn_id is not None:
            raise ValueError("direct ToolActivity forbids turn_id")
        return self


class ThreadView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    thread: Thread
    entries: tuple[ThreadEntry, ...] = ()
    turns: tuple[Turn, ...] = ()
    summary: ThreadSummary | None = None
    tool_activities: tuple[ToolActivity, ...] = ()
