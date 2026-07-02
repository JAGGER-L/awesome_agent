from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FirstRunState:
    env_file_exists: bool
    project_config_exists: bool
    local_config_exists: bool

    @property
    def needs_setup(self) -> bool:
        return not (
            self.env_file_exists
            and self.project_config_exists
            and self.local_config_exists
        )


def inspect_first_run_state(*, project_root: Path, home: Path) -> FirstRunState:
    return FirstRunState(
        env_file_exists=(project_root / ".env").exists(),
        project_config_exists=(project_root / "awesome-agent.yaml").exists(),
        local_config_exists=(home / ".awesome-agent" / "config.toml").exists(),
    )
