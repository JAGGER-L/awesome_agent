from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    model_validator,
)

from awesome_agent.modeling.messages import AssistantMessage, ModelMessage
from awesome_agent.modeling.tools import ToolChoice, ToolChoiceMode, ToolDefinition

type ProviderId = Literal["deepseek", "kimi"]
_MAX_JSON_SAFE_INTEGER = 9_007_199_254_740_991


class StopReason(StrEnum):
    COMPLETED = "completed"
    TOOL_CALLS = "tool_calls"
    MAX_TOKENS = "max_tokens"
    CONTENT_FILTER = "content_filter"
    UNKNOWN = "unknown"


class ContinuationState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderId
    kind: str = Field(min_length=1, max_length=128)
    schema_version: Literal[1] = 1
    data: JsonValue


class ModelUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0, le=_MAX_JSON_SAFE_INTEGER)
    output_tokens: int = Field(default=0, ge=0, le=_MAX_JSON_SAFE_INTEGER)
    reasoning_tokens: int = Field(default=0, ge=0, le=_MAX_JSON_SAFE_INTEGER)
    cache_read_tokens: int = Field(default=0, ge=0, le=_MAX_JSON_SAFE_INTEGER)
    cache_write_tokens: int = Field(default=0, ge=0, le=_MAX_JSON_SAFE_INTEGER)
    provider_retries: int = Field(default=0, ge=0, le=6)

    def __add__(self, other: ModelUsage) -> ModelUsage:
        return ModelUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            provider_retries=self.provider_retries + other.provider_retries,
        )


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[ModelMessage, ...] = Field(min_length=1)
    tools: tuple[ToolDefinition, ...] = ()
    tool_choice: ToolChoice = Field(default_factory=ToolChoice)
    max_output_tokens: int = Field(default=6_000, ge=1, le=262_144)
    thinking_enabled: StrictBool
    continuation: ContinuationState | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )

    @model_validator(mode="after")
    def validate_tool_selection(self) -> Self:
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("Tool definitions must have unique names.")
        if (
            self.tool_choice.mode is ToolChoiceMode.TOOL
            and self.tool_choice.name not in set(names)
        ):
            raise ValueError("Specific tool choice must reference a defined tool.")
        if self.tool_choice.mode is ToolChoiceMode.REQUIRED and not names:
            raise ValueError("Required tool choice needs at least one defined tool.")
        return self


class ModelTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderId
    model: str = Field(min_length=1, max_length=200)
    assistant: AssistantMessage
    stop_reason: StopReason
    usage: ModelUsage = Field(default_factory=ModelUsage)
    response_id: str | None = Field(default=None, max_length=512)
    continuation: ContinuationState | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )
