from __future__ import annotations

import os
import tempfile
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

from awesome_agent.repositories.policy import normalize_path


class LocalRepositoryConfig(BaseModel):
    version: int = 1
    allowed_roots: list[Path] = Field(default_factory=list)
    workspace_root: Path = Field(
        default_factory=lambda: Path.home() / ".awesome-agent" / "worktrees"
    )


class LocalRepositoryConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()

    def load(self) -> LocalRepositoryConfig:
        if not self.path.exists():
            return LocalRepositoryConfig()
        data = tomllib.loads(self.path.read_text(encoding="utf-8"))
        repositories = data.get("repositories", {})
        workspaces = data.get("workspaces", {})
        return LocalRepositoryConfig(
            version=int(data.get("version", 1)),
            allowed_roots=[
                normalize_path(Path(value))
                for value in repositories.get("allowed_roots", [])
            ],
            workspace_root=normalize_path(
                Path(
                    workspaces.get(
                        "root",
                        Path.home() / ".awesome-agent" / "worktrees",
                    )
                )
            ),
        )

    def add_root(self, root: Path) -> LocalRepositoryConfig:
        config = self.load()
        normalized = normalize_path(root)
        roots = list(config.allowed_roots)
        if normalized not in roots:
            roots.append(normalized)
        updated = config.model_copy(update={"allowed_roots": roots})
        self.save(updated)
        return updated

    def remove_root(self, root: Path) -> LocalRepositoryConfig:
        config = self.load()
        normalized = normalize_path(root)
        updated = config.model_copy(
            update={
                "allowed_roots": [
                    candidate
                    for candidate in config.allowed_roots
                    if candidate != normalized
                ]
            }
        )
        self.save(updated)
        return updated

    def save(self, config: LocalRepositoryConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = _serialize(config)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(
                file_descriptor, "w", encoding="utf-8", newline="\n"
            ) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)


def _serialize(config: LocalRepositoryConfig) -> str:
    roots = ",\n".join(f'  "{_toml_path(root)}"' for root in config.allowed_roots)
    root_block = f"[\n{roots}\n]" if roots else "[]"
    return (
        f"version = {config.version}\n\n"
        "[repositories]\n"
        f"allowed_roots = {root_block}\n\n"
        "[workspaces]\n"
        f'root = "{_toml_path(config.workspace_root)}"\n'
    )


def _toml_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace('"', '\\"')
