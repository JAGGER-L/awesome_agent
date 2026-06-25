from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from awesome_agent.modeling.errors import ModelErrorInfo
from awesome_agent.modeling.turns import ModelTurn


class ReasoningStarted(BaseModel):
    type: Literal["reasoning.started"] = "reasoning.started"


class ReasoningDelta(BaseModel):
    type: Literal["reasoning.delta"] = "reasoning.delta"
    text: str


class TextDelta(BaseModel):
    type: Literal["text.delta"] = "text.delta"
    text: str


class ToolCallStarted(BaseModel):
    type: Literal["tool_call.started"] = "tool_call.started"
    index: int = Field(ge=0)
    call_id: str
    name: str


class ToolArgumentsDelta(BaseModel):
    type: Literal["tool_arguments.delta"] = "tool_arguments.delta"
    index: int = Field(ge=0)
    text: str


class TurnCompleted(BaseModel):
    type: Literal["turn.completed"] = "turn.completed"
    turn: ModelTurn


class TurnFailed(BaseModel):
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
