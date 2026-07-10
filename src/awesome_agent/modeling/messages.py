from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.modeling.tools import ToolCall


class SystemMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system"] = "system"
    content: str = Field(min_length=1, max_length=1_000_000)


class UserMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["user"] = "user"
    content: str = Field(min_length=1, max_length=1_000_000)


class AssistantMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["assistant"] = "assistant"
    content: str = Field(default="", max_length=1_000_000)
    tool_calls: tuple[ToolCall, ...] = ()


class ToolResultMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["tool"] = "tool"
    call_id: str = Field(min_length=1, max_length=256)
    content: str = Field(max_length=1_000_000)
    is_error: bool = False
    artifact_refs: tuple[str, ...] = ()


ModelMessage = Annotated[
    SystemMessage | UserMessage | AssistantMessage | ToolResultMessage,
    Field(discriminator="role"),
]
