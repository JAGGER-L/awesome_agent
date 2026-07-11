from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AwesomePaths:
    """Resolved filesystem roots for local Awesome Agent operation."""

    home: Path
    install_dir: Path
    env_file: Path
    config_file: Path
    local_config_path: Path
    user_extension_config: Path
    skills_dir: Path
    memory_dir: Path
    user_memory_file: Path
    workspaces_dir: Path
    state_dir: Path
    application_db: Path
    checkpoint_db: Path
    change_journal_dir: Path
    ui_file: Path
    runs_dir: Path
    logs_dir: Path
    threads_dir: Path
    worktrees_dir: Path

    @classmethod
    def resolve(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        home: Path | None = None,
        platform: str | None = None,
    ) -> AwesomePaths:
        env = os.environ if environ is None else environ
        host_home = home or Path.home()
        awesome_home = _path_from_env(env, "AWESOME_HOME")
        resolved_platform = platform or sys.platform
        if awesome_home is None:
            awesome_home = _default_home(
                env=env,
                home=host_home,
                platform=resolved_platform,
            )
        install_dir = _path_from_env(env, "AWESOME_INSTALL_DIR") or (
            _default_install_dir(
                env=env,
                home=host_home,
                platform=resolved_platform,
            )
        )
        return cls.from_home(awesome_home, install_dir=install_dir)

    @classmethod
    def from_home(
        cls,
        home: Path,
        *,
        install_dir: Path | None = None,
    ) -> AwesomePaths:
        resolved_home = Path(home).expanduser()
        resolved_install_dir = (
            Path(install_dir).expanduser()
            if install_dir is not None
            else resolved_home / "app"
        )
        state_dir = resolved_home / "state"
        return cls(
            home=resolved_home,
            install_dir=resolved_install_dir,
            env_file=resolved_home / ".env",
            config_file=resolved_home / "config.yaml",
            local_config_path=resolved_home / "config.toml",
            user_extension_config=resolved_home / "awesome-agent.yaml",
            skills_dir=resolved_home / "skills",
            memory_dir=resolved_home / "memory",
            user_memory_file=resolved_home / "memory" / "USER.md",
            workspaces_dir=resolved_home / "workspaces",
            state_dir=state_dir,
            application_db=state_dir / "application.db",
            checkpoint_db=state_dir / "checkpoints.db",
            change_journal_dir=state_dir / "change-journal",
            ui_file=resolved_home / "ui.json",
            runs_dir=resolved_home / "runs",
            logs_dir=resolved_home / "logs",
            threads_dir=resolved_home / "threads",
            worktrees_dir=resolved_home / "worktrees",
        )

    def workspace_config_file(self, workspace: Path) -> Path:
        """Return the only supported project configuration file."""

        return Path(workspace).expanduser() / ".awesome" / "config.yaml"

    def workspace_memory_file(self, workspace_key: str) -> Path:
        if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", workspace_key) is None:
            raise ValueError("workspace_key is not a safe opaque identifier")
        return self.workspaces_dir / workspace_key / "MEMORY.md"


def awesome_paths() -> AwesomePaths:
    return AwesomePaths.resolve()


def _path_from_env(env: Mapping[str, str], name: str) -> Path | None:
    value = env.get(name)
    if value is None or not value.strip():
        return None
    return Path(value).expanduser()


def _default_home(
    *,
    env: Mapping[str, str],
    home: Path,
    platform: str,
) -> Path:
    if platform.startswith("win"):
        localappdata = env.get("LOCALAPPDATA")
        base = Path(localappdata) if localappdata else home / "AppData" / "Local"
        return base / "Awesome"
    return home / ".awesome"


def _default_install_dir(
    *,
    env: Mapping[str, str],
    home: Path,
    platform: str,
) -> Path:
    if platform.startswith("win"):
        localappdata = env.get("LOCALAPPDATA")
        base = Path(localappdata) if localappdata else home / "AppData" / "Local"
        return base / "Programs" / "Awesome"
    return home / ".local" / "share" / "awesome"
