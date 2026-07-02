from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SlashCommandKind(StrEnum):
    NEW = "new"
    THREADS = "threads"
    RESUME = "resume"
    STATUS = "status"
    MODELS = "models"
    SKILLS = "skills"
    TOOLS = "tools"
    MCP = "mcp"
    MEMORY = "memory"
    UPLOADS = "uploads"
    ARTIFACTS = "artifacts"
    DETAILS = "details"
    USAGE = "usage"
    CONFIG = "config"
    HELP = "help"
    QUIT = "quit"
    USER_MESSAGE = "user_message"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SlashCommand:
    kind: SlashCommandKind
    argument: str = ""


COMMAND_DESCRIPTIONS: dict[SlashCommandKind, str] = {
    SlashCommandKind.NEW: "Start a new local conversation.",
    SlashCommandKind.THREADS: "List known threads.",
    SlashCommandKind.RESUME: "Resume a thread by id or title.",
    SlashCommandKind.STATUS: "Show the current thread, run, API, and sandbox status.",
    SlashCommandKind.MODELS: "List configured model profiles.",
    SlashCommandKind.SKILLS: "Browse enabled and available skills.",
    SlashCommandKind.TOOLS: "Show built-in, MCP, and sandbox tools.",
    SlashCommandKind.MCP: "Show MCP server status.",
    SlashCommandKind.MEMORY: "Show memory configuration and current memory summary.",
    SlashCommandKind.UPLOADS: "Show uploaded files for this thread.",
    SlashCommandKind.ARTIFACTS: "Show generated artifacts.",
    SlashCommandKind.DETAILS: "Toggle verbose activity rendering.",
    SlashCommandKind.USAGE: "Show token usage and context.",
    SlashCommandKind.CONFIG: "Show resolved config paths and overrides.",
    SlashCommandKind.HELP: "Show interactive help.",
    SlashCommandKind.QUIT: "Exit the TUI.",
}

ALIASES: dict[str, SlashCommandKind] = {
    "switch": SlashCommandKind.THREADS,
    "model": SlashCommandKind.MODELS,
}


def parse_slash_command(raw: str) -> SlashCommand:
    stripped = raw.strip()
    if not stripped.startswith("/"):
        return SlashCommand(SlashCommandKind.USER_MESSAGE, stripped)
    command_text = stripped[1:]
    if not command_text:
        return SlashCommand(SlashCommandKind.UNKNOWN, "")
    name, _, argument = command_text.partition(" ")
    alias = ALIASES.get(name)
    if alias is not None:
        return SlashCommand(alias, argument.strip())
    try:
        kind = SlashCommandKind(name)
    except ValueError:
        return SlashCommand(SlashCommandKind.UNKNOWN, command_text)
    return SlashCommand(kind, argument.strip())


def slash_command_help() -> str:
    lines = ["Commands:"]
    for kind, description in COMMAND_DESCRIPTIONS.items():
        lines.append(f"/{kind.value} - {description}")
    return "\n".join(lines)
