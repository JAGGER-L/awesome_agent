from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from awesome_agent.modeling.tools import ToolCall


class SystemMessage(BaseModel):
    role: Literal["system"] = "system"
    content: str


class UserMessage(BaseModel):
    role: Literal["user"] = "user"
    content: str


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ToolResultMessage(BaseModel):
    role: Literal["tool"] = "tool"
    call_id: str = Field(min_length=1)
    content: str
    is_error: bool = False
    artifact_refs: list[str] = Field(default_factory=list)


ModelMessage = Annotated[
    SystemMessage | UserMessage | AssistantMessage | ToolResultMessage,
    Field(discriminator="role"),
]
