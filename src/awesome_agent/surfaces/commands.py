from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SlashCommandKind(StrEnum):
    NEW = "new"
    THREADS = "threads"
    ATTACH = "attach"
    STATUS = "status"
    MODEL = "model"
    THINKING = "thinking"
    SKILLS = "skills"
    TOOLS = "tools"
    MCP = "mcp"
    MEMORY = "memory"
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
        description="Start a new conversation.",
        category="thread",
        argument_hint="[title]",
    ),
    SlashCommandDefinition(
        name="threads",
        kind=SlashCommandKind.THREADS,
        description="Switch conversation.",
        category="thread",
    ),
    SlashCommandDefinition(
        name="attach",
        kind=SlashCommandKind.ATTACH,
        description="Attach a local file to the next turn.",
        category="context",
        argument_hint="<path>",
        requires_thread=True,
    ),
    SlashCommandDefinition(
        name="status",
        kind=SlashCommandKind.STATUS,
        description="Show current state.",
        category="status",
    ),
    SlashCommandDefinition(
        name="model",
        kind=SlashCommandKind.MODEL,
        description="Choose model.",
        category="model",
    ),
    SlashCommandDefinition(
        name="thinking",
        kind=SlashCommandKind.THINKING,
        description="Choose thinking mode.",
        category="status",
    ),
    SlashCommandDefinition(
        name="skills",
        kind=SlashCommandKind.SKILLS,
        description="Apply skills to the next turn.",
        category="extensions",
    ),
    SlashCommandDefinition(
        name="tools",
        kind=SlashCommandKind.TOOLS,
        description="Show leader-visible tools.",
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
        description="Manage memory.",
        category="context",
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
        description="Show configuration.",
        category="status",
    ),
    SlashCommandDefinition(
        name="help",
        kind=SlashCommandKind.HELP,
        description="Show commands.",
        category="help",
    ),
    SlashCommandDefinition(
        name="quit",
        kind=SlashCommandKind.QUIT,
        description="Exit.",
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
