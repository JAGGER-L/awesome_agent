from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


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
    MCP = "mcp"
    MEMORY = "memory"
    STATUS = "status"
    USAGE = "usage"
    DOCTOR = "doctor"
    CONFIG = "config"
    PERMISSIONS = "permissions"
    INIT = "init"
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
            CommandName.MCP,
            CommandName.MEMORY,
            CommandName.STATUS,
            CommandName.USAGE,
            CommandName.DOCTOR,
            CommandName.CONFIG,
            CommandName.PERMISSIONS,
        )
    },
    CommandName.INIT: CommandOwner.SKILL,
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


class CommandIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: CommandName
    arguments: tuple[str, ...] = ()
