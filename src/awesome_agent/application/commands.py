from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class CommandOwner(StrEnum):
    APPLICATION = "application"
    INK = "ink"


class CommandName(StrEnum):
    NEW = "new"
    RENAME = "rename"
    RESUME = "resume"
    FORK = "fork"
    RETRY = "retry"
    SEARCH = "search"
    EXPORT = "export"
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
    WEB = "web"
    MEMORY = "memory"
    STATUS = "status"
    USAGE = "usage"
    DOCTOR = "doctor"
    CONFIG = "config"
    PERMISSIONS = "permissions"
    HELP = "help"
    THEME = "theme"
    COPY = "copy"
    QUIT = "quit"


COMMAND_OWNERS: dict[CommandName, CommandOwner] = {
    **{
        name: CommandOwner.APPLICATION
        for name in (
            CommandName.NEW,
            CommandName.RENAME,
            CommandName.RESUME,
            CommandName.FORK,
            CommandName.RETRY,
            CommandName.SEARCH,
            CommandName.EXPORT,
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
            CommandName.WEB,
            CommandName.MEMORY,
            CommandName.STATUS,
            CommandName.USAGE,
            CommandName.DOCTOR,
            CommandName.CONFIG,
            CommandName.PERMISSIONS,
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


class CommandIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: CommandName
    arguments: tuple[str, ...] = ()
