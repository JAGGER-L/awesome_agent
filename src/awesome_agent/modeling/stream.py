from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.modeling.errors import ModelErrorInfo
from awesome_agent.modeling.turns import ModelTurn


class ReasoningStarted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["reasoning.started"] = "reasoning.started"


class ReasoningDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["reasoning.delta"] = "reasoning.delta"
    text: str = Field(min_length=1, max_length=1_000_000)


class TextDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["text.delta"] = "text.delta"
    text: str = Field(min_length=1, max_length=1_000_000)


class ToolCallStarted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["tool_call.started"] = "tool_call.started"
    index: int = Field(ge=0)
    call_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=200)


class ToolArgumentsDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["tool_arguments.delta"] = "tool_arguments.delta"
    index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=1_000_000)


class TurnCompleted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["turn.completed"] = "turn.completed"
    turn: ModelTurn


class TurnFailed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["turn.failed"] = "turn.failed"
    error: ModelErrorInfo


ModelStreamEvent = Annotated[
    ReasoningStarted
    | ReasoningDelta
    | TextDelta
    | ToolCallStarted
    | ToolArgumentsDelta
    | TurnCompleted
    | TurnFailed,
    Field(discriminator="type"),
]
