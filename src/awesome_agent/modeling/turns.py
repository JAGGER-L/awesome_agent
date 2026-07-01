from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, JsonValue

from awesome_agent.modeling.messages import AssistantMessage, ModelMessage
from awesome_agent.modeling.tools import ToolChoice, ToolDefinition


class StopReason(StrEnum):
    COMPLETED = "completed"
    TOOL_CALLS = "tool_calls"
    MAX_TOKENS = "max_tokens"
    CONTENT_FILTER = "content_filter"
    UNKNOWN = "unknown"


class ReasoningStatus(StrEnum):
    STARTED = "started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"


class ReasoningSegment(BaseModel):
    sequence: int = Field(ge=1)
    text: str


class ReasoningTrace(BaseModel):
    status: ReasoningStatus
    segments: list[ReasoningSegment] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(segment.text for segment in self.segments)


class ContinuationState(BaseModel):
    provider: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    schema_version: int = Field(default=1, ge=1)
    data: JsonValue


class ModelUsage(BaseModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)


class ModelRequest(BaseModel):
    messages: list[ModelMessage] = Field(min_length=1)
    tools: list[ToolDefinition] = Field(default_factory=list)
    tool_choice: ToolChoice = Field(default_factory=ToolChoice)
    max_output_tokens: int = Field(default=6000, ge=1)
    continuation: ContinuationState | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )


class ModelTurn(BaseModel):
    assistant: AssistantMessage
    stop_reason: StopReason
    model: str
    provider: str
    response_id: str | None = None
    usage: ModelUsage = Field(default_factory=ModelUsage)
    reasoning: ReasoningTrace | None = None
    continuation: ContinuationState | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )
