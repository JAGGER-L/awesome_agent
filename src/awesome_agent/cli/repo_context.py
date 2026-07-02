from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class CliLaunchContext:
    project_root: Path
    context_kind: Literal["repo", "workspace"]
    git_root: Path | None = None

    @property
    def display_path(self) -> str:
        return str(self.git_root or self.project_root)


def discover_launch_context(project_root: Path) -> CliLaunchContext:
    resolved = project_root.resolve()
    git_root = _find_git_root(resolved)
    if git_root is not None:
        return CliLaunchContext(
            project_root=resolved,
            context_kind="repo",
            git_root=git_root,
        )
    return CliLaunchContext(project_root=resolved, context_kind="workspace")


def _find_git_root(start: Path) -> Path | None:
    current = start
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        marker = candidate / ".git"
        if marker.exists():
            return candidate.resolve()
    return None
