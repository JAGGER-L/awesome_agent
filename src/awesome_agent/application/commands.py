from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class CommandOwner(StrEnum):
    APPLICATION = "application"
    SKILL = "skill"
    INK = "ink"


class CommandName(StrEnum):
    NEW = "new"
    RESUME = "resume"
    HISTORY = "history"
    CONTEXT = "context"
    COMPACT = "compact"
    MODEL = "model"
    MODE = "mode"
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
    DOCTOR = "doctor"
    CONFIG = "config"
    INIT = "init"
    REVIEW = "review"
    DEBUG = "debug"
    TEST = "test"
    COMMIT = "commit"
    HELP = "help"
    THEME = "theme"
    DETAILS = "details"
    COPY = "copy"
    EDITOR = "editor"
    QUIT = "quit"


COMMAND_OWNERS: dict[CommandName, CommandOwner] = {
    **{
        name: CommandOwner.APPLICATION
        for name in (
            CommandName.NEW,
            CommandName.RESUME,
            CommandName.HISTORY,
            CommandName.CONTEXT,
            CommandName.COMPACT,
            CommandName.MODEL,
            CommandName.MODE,
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
            CommandName.DETAILS,
            CommandName.COPY,
            CommandName.EDITOR,
            CommandName.QUIT,
        )
    },
}


class CommandStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    INTERACTION_REQUIRED = "interaction_required"


class CommandIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: CommandName
    arguments: tuple[str, ...] = ()


class CommandResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CommandStatus
    content: str = Field(default="", max_length=30_000)
    data: dict[str, JsonValue] = Field(default_factory=dict)
