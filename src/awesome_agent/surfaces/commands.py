from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SlashCommandKind(StrEnum):
    NEW = "new"
    THREADS = "threads"
    RESUME = "resume"
    RUN = "run"
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


@dataclass(frozen=True, slots=True)
class SlashCommandDefinition:
    name: str
    kind: SlashCommandKind
    description: str
    category: str
    argument_hint: str = ""
    requires_thread: bool = False
    executor: str = "client"
    output_kind: str = "message"
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def display_name(self) -> str:
        return f"/{self.name}"


COMMAND_DEFINITIONS: tuple[SlashCommandDefinition, ...] = (
    SlashCommandDefinition(
        name="new",
        kind=SlashCommandKind.NEW,
        description="Start a new local conversation.",
        category="thread",
        argument_hint="[title]",
    ),
    SlashCommandDefinition(
        name="threads",
        kind=SlashCommandKind.THREADS,
        description="List known threads.",
        category="thread",
        aliases=("switch",),
    ),
    SlashCommandDefinition(
        name="resume",
        kind=SlashCommandKind.RESUME,
        description="Resume a thread by id or title.",
        category="thread",
        argument_hint="<id-or-title>",
    ),
    SlashCommandDefinition(
        name="run",
        kind=SlashCommandKind.RUN,
        description="Start a Coding Run from the current thread context.",
        category="run",
        argument_hint="<goal>",
        requires_thread=True,
        executor="api",
        output_kind="run",
    ),
    SlashCommandDefinition(
        name="status",
        kind=SlashCommandKind.STATUS,
        description="Show the current thread, run, API, and sandbox status.",
        category="status",
    ),
    SlashCommandDefinition(
        name="models",
        kind=SlashCommandKind.MODELS,
        description="List configured model profiles.",
        category="status",
        aliases=("model",),
    ),
    SlashCommandDefinition(
        name="skills",
        kind=SlashCommandKind.SKILLS,
        description="Browse enabled and available skills.",
        category="extensions",
    ),
    SlashCommandDefinition(
        name="tools",
        kind=SlashCommandKind.TOOLS,
        description="Show built-in, MCP, and sandbox tools.",
        category="extensions",
    ),
    SlashCommandDefinition(
        name="mcp",
        kind=SlashCommandKind.MCP,
        description="Show MCP server status.",
        category="extensions",
    ),
    SlashCommandDefinition(
        name="memory",
        kind=SlashCommandKind.MEMORY,
        description="Show memory configuration and current memory summary.",
        category="context",
    ),
    SlashCommandDefinition(
        name="uploads",
        kind=SlashCommandKind.UPLOADS,
        description="Show uploaded files for this thread.",
        category="context",
        requires_thread=True,
    ),
    SlashCommandDefinition(
        name="artifacts",
        kind=SlashCommandKind.ARTIFACTS,
        description="Show generated artifacts.",
        category="context",
        requires_thread=True,
    ),
    SlashCommandDefinition(
        name="details",
        kind=SlashCommandKind.DETAILS,
        description="Toggle verbose activity rendering.",
        category="display",
    ),
    SlashCommandDefinition(
        name="usage",
        kind=SlashCommandKind.USAGE,
        description="Show token usage and context.",
        category="status",
    ),
    SlashCommandDefinition(
        name="config",
        kind=SlashCommandKind.CONFIG,
        description="Show resolved config paths and overrides.",
        category="status",
    ),
    SlashCommandDefinition(
        name="help",
        kind=SlashCommandKind.HELP,
        description="Show interactive help.",
        category="help",
    ),
    SlashCommandDefinition(
        name="quit",
        kind=SlashCommandKind.QUIT,
        description="Exit the TUI.",
        category="session",
    ),
)

COMMANDS_BY_NAME = {definition.name: definition for definition in COMMAND_DEFINITIONS}
COMMAND_DESCRIPTIONS = {
    definition.kind: definition.description for definition in COMMAND_DEFINITIONS
}
ALIASES = {
    alias: definition.kind
    for definition in COMMAND_DEFINITIONS
    for alias in definition.aliases
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
    definition = COMMANDS_BY_NAME.get(name)
    if definition is None:
        return SlashCommand(SlashCommandKind.UNKNOWN, command_text)
    return SlashCommand(definition.kind, argument.strip())


def command_suggestions(prefix: str) -> list[SlashCommandDefinition]:
    stripped = prefix.lstrip()
    if not stripped.startswith("/"):
        return []
    command_prefix = stripped[1:]
    if " " in command_prefix:
        return []
    if not command_prefix:
        return list(COMMAND_DEFINITIONS)
    direct_matches = [
        definition
        for definition in COMMAND_DEFINITIONS
        if definition.name.startswith(command_prefix)
    ]
    alias_matches = [
        definition
        for definition in COMMAND_DEFINITIONS
        if definition not in direct_matches
        and any(alias.startswith(command_prefix) for alias in definition.aliases)
    ]
    return [*direct_matches, *alias_matches]


def slash_command_help() -> str:
    lines = ["Commands:"]
    for definition in COMMAND_DEFINITIONS:
        lines.append(f"/{definition.name} - {definition.description}")
    return "\n".join(lines)
