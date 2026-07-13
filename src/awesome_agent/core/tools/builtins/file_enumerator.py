from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from awesome_agent.core.tools.context import ToolExecutionContext
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.policy import SafeWorkspacePath, resolve_workspace_path

DEFAULT_PRUNED_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)


class ScanCancelled(RuntimeError):
    pass


class ScanCancellation:
    def __init__(self) -> None:
        self._cancelled = Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def raise_if_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise ScanCancelled


@dataclass(frozen=True, slots=True)
class EnumeratedFile:
    relative: str
    resolved: Path


def enumerate_workspace_files(
    root: SafeWorkspacePath,
    context: ToolExecutionContext,
    *,
    cancellation: ScanCancellation,
    prune_defaults: bool,
) -> Iterator[EnumeratedFile]:
    workspace = context.workspace.canonical_path
    for current, directory_names, file_names in os.walk(
        root.resolved,
        followlinks=False,
    ):
        cancellation.raise_if_cancelled()
        current_path = Path(current)
        safe_directories: list[str] = []
        for name in sorted(
            directory_names,
            key=lambda value: (value.casefold(), value),
        ):
            cancellation.raise_if_cancelled()
            child = current_path / name
            if child.is_symlink() or (
                prune_defaults and name.casefold() in DEFAULT_PRUNED_DIRECTORIES
            ):
                continue
            relative = child.relative_to(workspace).as_posix()
            try:
                resolve_workspace_path(
                    context.workspace,
                    relative,
                    must_exist=True,
                    expected_kind="directory",
                )
            except ExpectedToolFailure:
                continue
            safe_directories.append(name)
        directory_names[:] = safe_directories

        for name in sorted(file_names, key=lambda value: (value.casefold(), value)):
            cancellation.raise_if_cancelled()
            path = current_path / name
            if path.is_symlink():
                continue
            relative = path.relative_to(workspace).as_posix()
            try:
                safe = resolve_workspace_path(
                    context.workspace,
                    relative,
                    must_exist=True,
                    expected_kind="file",
                )
            except ExpectedToolFailure:
                continue
            yield EnumeratedFile(relative=relative, resolved=safe.resolved)
