from __future__ import annotations

import hashlib
import os
import shlex
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PureWindowsPath

from awesome_agent.core.tools.errors import ToolControlFlow


class CommandPolicyAction(StrEnum):
    ALLOW = "allow"
    INTERACTION_REQUIRED = "interaction_required"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class CommandPolicyDecision:
    action: CommandPolicyAction
    reason: str
    scope: str | None = None


class InteractionRequired(ToolControlFlow):
    def __init__(self, *, scope: str, prompt: str) -> None:
        super().__init__(prompt)
        self.scope = scope
        self.prompt = prompt


_ELEVATION_COMMANDS = {"doas", "runas", "su", "sudo"}
_SHUTDOWN_COMMANDS = {"halt", "poweroff", "reboot", "shutdown"}
_DISK_COMMANDS = {
    "diskpart",
    "fdisk",
    "format",
    "format.com",
    "mkfs",
    "parted",
}
_DESTRUCTIVE_COMMANDS = {"del", "erase", "rd", "rm", "rmdir"}


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return []


def _command_name(token: str) -> str:
    return Path(token.strip("\"'")).name.casefold()


def _absolute_path(token: str) -> Path | None:
    cleaned = token.strip("\"'")
    windows = PureWindowsPath(cleaned)
    if windows.is_absolute():
        return Path(str(windows))
    native = Path(cleaned)
    if native.is_absolute():
        return native
    if cleaned in {"/", "\\"}:
        return Path(cleaned)
    return None


def _is_filesystem_root(path: Path) -> bool:
    rendered = str(path)
    if rendered in {"/", "\\"}:
        return True
    windows = PureWindowsPath(rendered)
    return (
        bool(windows.anchor)
        and rendered.rstrip("/\\").casefold() == windows.anchor.rstrip("/\\").casefold()
    )


def evaluate_command(command: str, workspace: Path) -> CommandPolicyDecision:
    tokens = _tokens(command)
    if not tokens:
        return CommandPolicyDecision(
            CommandPolicyAction.DENY,
            "Command could not be parsed safely.",
        )
    name = _command_name(tokens[0])
    if name in _ELEVATION_COMMANDS:
        return CommandPolicyDecision(
            CommandPolicyAction.DENY,
            "Privilege elevation commands are not allowed.",
        )
    if name in _SHUTDOWN_COMMANDS:
        return CommandPolicyDecision(
            CommandPolicyAction.DENY,
            "Host shutdown and reboot commands are not allowed.",
        )
    if name in _DISK_COMMANDS or name.startswith("mkfs."):
        return CommandPolicyDecision(
            CommandPolicyAction.DENY,
            "Disk formatting and partition commands are not allowed.",
        )

    workspace = workspace.resolve()
    detected: list[Path] = []
    for token in tokens[1:]:
        path = _absolute_path(token)
        if path is None:
            continue
        if _is_filesystem_root(path) and name in _DESTRUCTIVE_COMMANDS:
            return CommandPolicyDecision(
                CommandPolicyAction.DENY,
                "Destructive commands cannot target a filesystem root.",
            )
        resolved = path.resolve(strict=False)
        if resolved == workspace and name in _DESTRUCTIVE_COMMANDS:
            return CommandPolicyDecision(
                CommandPolicyAction.DENY,
                "Destructive commands cannot target the workspace root.",
            )
        if not resolved.is_relative_to(workspace):
            detected.append(resolved)

    scope = f"execute_command_{hashlib.sha256(command.encode()).hexdigest()[:24]}"
    reason = (
        "Command references an absolute path outside the workspace."
        if detected
        else "Agent shell commands require explicit approval."
    )
    return CommandPolicyDecision(
        CommandPolicyAction.INTERACTION_REQUIRED,
        reason,
        scope=scope,
    )
