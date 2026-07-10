from __future__ import annotations

import re
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

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,199}$")


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str = Field(default="", max_length=10_000)
    input_schema: dict[str, JsonValue]

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if _TOOL_NAME.fullmatch(value) is None:
            raise ValueError("Tool name is invalid.")
        return value


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str = Field(min_length=1, max_length=256)
    name: str
    arguments_json: str = Field(max_length=1_000_000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if _TOOL_NAME.fullmatch(value) is None:
            raise ValueError("Tool name is invalid.")
        return value


class ToolChoiceMode(StrEnum):
    AUTO = "auto"
    NONE = "none"
    REQUIRED = "required"
    TOOL = "tool"


class ToolChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: ToolChoiceMode = ToolChoiceMode.AUTO
    name: str | None = None

    @model_validator(mode="after")
    def validate_specific_tool(self) -> Self:
        if self.mode is ToolChoiceMode.TOOL and not self.name:
            raise ValueError("A specific tool choice requires a tool name.")
        if self.mode is not ToolChoiceMode.TOOL and self.name is not None:
            raise ValueError("Only a specific tool choice may include a name.")
        if self.name is not None and _TOOL_NAME.fullmatch(self.name) is None:
            raise ValueError("Tool choice name is invalid.")
        return self
