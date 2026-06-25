from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, JsonValue, model_validator


class ToolDefinition(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, JsonValue]


class ToolCall(BaseModel):
    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments_json: str


class ToolChoiceMode(StrEnum):
    AUTO = "auto"
    NONE = "none"
    REQUIRED = "required"
    TOOL = "tool"


class ToolChoice(BaseModel):
    mode: ToolChoiceMode = ToolChoiceMode.AUTO
    name: str | None = None

    @model_validator(mode="after")
    def validate_specific_tool(self) -> ToolChoice:
        if self.mode is ToolChoiceMode.TOOL and not self.name:
            raise ValueError("A specific tool choice requires a tool name.")
        if self.mode is not ToolChoiceMode.TOOL and self.name is not None:
            raise ValueError("Only a specific tool choice may include a name.")
        return self
