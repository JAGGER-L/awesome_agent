from __future__ import annotations

import os
import re
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

type WorkspacePathPlatform = Literal["windows", "posix"]

_WINDOWS_RESERVED_BASENAMES = frozenset(
    {
        "aux",
        "clock$",
        "con",
        "conin$",
        "conout$",
        "nul",
        "prn",
    }
)
_WINDOWS_NUMBERED_DEVICE = re.compile(r"(?:com|lpt)[1-9¹²³]", re.IGNORECASE)
_WINDOWS_SHORT_NAME_ALIAS = re.compile(
    r"[^.]{1,6}~[0-9]+(?:\.[^.]{0,3})?",
    re.IGNORECASE,
)
_WINDOWS_INVALID_CHARACTERS = frozenset('<>"|?*')


class WorkspacePathSyntaxKind(StrEnum):
    ESCAPE = "escape"
    AMBIGUOUS = "ambiguous"


class WorkspacePathSyntaxError(ValueError):
    def __init__(self, kind: WorkspacePathSyntaxKind) -> None:
        super().__init__(kind.value)
        self.kind = kind


def workspace_path_platform() -> WorkspacePathPlatform:
    return "windows" if os.name == "nt" else "posix"


def validate_workspace_relative_path_syntax(
    requested: str,
    *,
    platform: WorkspacePathPlatform | None = None,
) -> None:
    selected = platform or workspace_path_platform()
    if "\x00" in requested:
        raise WorkspacePathSyntaxError(WorkspacePathSyntaxKind.AMBIGUOUS)
    parsed = (
        PureWindowsPath(requested)
        if selected == "windows"
        else PurePosixPath(requested)
    )
    if (
        parsed.is_absolute()
        or requested.startswith("/")
        or (selected == "windows" and requested.startswith("\\"))
        or parsed.drive
        or parsed.root
        or parsed.anchor
        or ".." in parsed.parts
    ):
        raise WorkspacePathSyntaxError(WorkspacePathSyntaxKind.ESCAPE)
    if selected == "windows" and any(
        _is_ambiguous_windows_component(component) for component in parsed.parts
    ):
        raise WorkspacePathSyntaxError(WorkspacePathSyntaxKind.AMBIGUOUS)


def _is_ambiguous_windows_component(component: str) -> bool:
    if (
        ":" in component
        or component.endswith((" ", "."))
        or any(character in _WINDOWS_INVALID_CHARACTERS for character in component)
        or any(ord(character) < 32 for character in component)
    ):
        return True
    basename = component.split(".", maxsplit=1)[0].rstrip(" .")
    return (
        basename.casefold() in _WINDOWS_RESERVED_BASENAMES
        or _WINDOWS_NUMBERED_DEVICE.fullmatch(basename) is not None
        or _WINDOWS_SHORT_NAME_ALIAS.fullmatch(component) is not None
    )
