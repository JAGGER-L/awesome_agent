from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class CommandOwner(StrEnum):
    APPLICATION = "application"
    SKILL = "skill"
    INK = "ink"


class CommandName(StrEnum):
    NEW = "new"
    RESUME = "resume"
    CONTEXT = "context"
    COMPACT = "compact"
    AUTH = "auth"
    MODEL = "model"
    THINKING = "thinking"
    WORKSPACE = "workspace"
    DIFF = "diff"
    UNDO = "undo"
    REDO = "redo"
    TOOLS = "tools"
    SKILLS = "skills"
    SKILL = "skill"
    MCP = "mcp"
    MEMORY = "memory"
    STATUS = "status"
    USAGE = "usage"
    DOCTOR = "doctor"
    CONFIG = "config"
    INIT = "init"
    REVIEW = "review"
    DEBUG = "debug"
    TEST = "test"
    COMMIT = "commit"
    HELP = "help"
    THEME = "theme"
    COPY = "copy"
    QUIT = "quit"


COMMAND_OWNERS: dict[CommandName, CommandOwner] = {
    **{
        name: CommandOwner.APPLICATION
        for name in (
            CommandName.NEW,
            CommandName.RESUME,
            CommandName.CONTEXT,
            CommandName.COMPACT,
            CommandName.AUTH,
            CommandName.MODEL,
            CommandName.THINKING,
            CommandName.WORKSPACE,
            CommandName.DIFF,
            CommandName.UNDO,
            CommandName.REDO,
            CommandName.TOOLS,
            CommandName.SKILLS,
            CommandName.SKILL,
            CommandName.MCP,
            CommandName.MEMORY,
            CommandName.STATUS,
            CommandName.USAGE,
            CommandName.DOCTOR,
            CommandName.CONFIG,
        )
    },
    **{
        name: CommandOwner.SKILL
        for name in (
            CommandName.INIT,
            CommandName.REVIEW,
            CommandName.DEBUG,
            CommandName.TEST,
            CommandName.COMMIT,
        )
    },
    **{
        name: CommandOwner.INK
        for name in (
            CommandName.HELP,
            CommandName.THEME,
            CommandName.COPY,
            CommandName.QUIT,
        )
    },
}


class CommandStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    INTERACTION_REQUIRED = "interaction_required"


class CommandIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: CommandName
    arguments: tuple[str, ...] = ()


class CommandOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1_000)
    selected: bool = False


class CommandSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt: str = Field(min_length=1, max_length=1_000)
    options: tuple[CommandOption, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_options(self) -> Self:
        values = [option.value for option in self.options]
        if len(values) != len(set(values)):
            raise ValueError("Command option values must be unique.")
        if sum(option.selected for option in self.options) > 1:
            raise ValueError("At most one Command option may be selected.")
        return self


class CommandSecretPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["deepseek", "kimi"]
    action: Literal["add", "replace"]
    label: str = Field(min_length=1, max_length=200)
    environment_variable: str = Field(min_length=1, max_length=128)
    help_url: str = Field(min_length=1, max_length=2_000)


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: CommandStatus
    content: str = Field(default="", max_length=30_000)
    data: dict[str, JsonValue] = Field(default_factory=dict)
    selection: CommandSelection | None = None
    secret_prompt: CommandSecretPrompt | None = None

    @model_validator(mode="after")
    def validate_interaction(self) -> Self:
        if self.selection is not None and self.secret_prompt is not None:
            raise ValueError("Command result cannot contain two input requests.")
        return self
