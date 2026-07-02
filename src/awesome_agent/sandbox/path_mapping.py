from __future__ import annotations

from pathlib import Path, PurePosixPath


class WorkspacePathMapper:
    logical_workspace = PurePosixPath("/mnt/user-data/workspace")

    def __init__(self, *, thread_workspace: Path) -> None:
        self.thread_workspace = thread_workspace.resolve()

    def to_host_path(self, logical_path: str | Path) -> Path:
        normalized = _to_posix(logical_path)
        try:
            relative = normalized.relative_to(self.logical_workspace)
        except ValueError as error:
            raise ValueError(
                f"path {normalized} is outside {self.logical_workspace}"
            ) from error
        return (self.thread_workspace / Path(*relative.parts)).resolve()


def _to_posix(path: str | Path) -> PurePosixPath:
    text = str(path).replace("\\", "/")
    if len(text) >= 2 and text[1] == ":":
        raise ValueError(f"host path {text} cannot be mapped as a logical path")
    return PurePosixPath(text)
