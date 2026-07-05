from __future__ import annotations

import os
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
    state_dir: Path
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
        if awesome_home is None:
            awesome_home = _default_home(
                env=env,
                home=host_home,
                platform=platform or sys.platform,
            )
        install_dir = _path_from_env(env, "AWESOME_INSTALL_DIR") or (
            awesome_home / "app"
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
        return cls(
            home=resolved_home,
            install_dir=resolved_install_dir,
            env_file=resolved_home / ".env",
            config_file=resolved_home / "config.yaml",
            local_config_path=resolved_home / "config.toml",
            user_extension_config=resolved_home / "awesome-agent.yaml",
            skills_dir=resolved_home / "skills",
            state_dir=resolved_home / "state",
            runs_dir=resolved_home / "runs",
            logs_dir=resolved_home / "logs",
            threads_dir=resolved_home / "threads",
            worktrees_dir=resolved_home / "worktrees",
        )


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
        return base / "awesome-agent"
    return home / ".awesome-agent"
