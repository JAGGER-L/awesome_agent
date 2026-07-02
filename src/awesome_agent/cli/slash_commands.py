from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SlashCommandKind(StrEnum):
    NEW = "new"
    STATUS = "status"
    MODELS = "models"
    MEMORY = "memory"
    HELP = "help"
    USER_MESSAGE = "user_message"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SlashCommand:
    kind: SlashCommandKind
    argument: str = ""


COMMAND_DESCRIPTIONS: dict[SlashCommandKind, str] = {
    SlashCommandKind.NEW: "Start a new local conversation.",
    SlashCommandKind.STATUS: "Show the current thread, run, API, and sandbox status.",
    SlashCommandKind.MODELS: "List configured model profiles.",
    SlashCommandKind.MEMORY: "Show memory configuration and current memory summary.",
    SlashCommandKind.HELP: "Show interactive help.",
}


def parse_slash_command(raw: str) -> SlashCommand:
    stripped = raw.strip()
    if not stripped.startswith("/"):
        return SlashCommand(SlashCommandKind.USER_MESSAGE, stripped)
    command_text = stripped[1:]
    if not command_text:
        return SlashCommand(SlashCommandKind.UNKNOWN, "")
    name, _, argument = command_text.partition(" ")
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
