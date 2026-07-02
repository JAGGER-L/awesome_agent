from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from awesome_agent.cli.config_flow import user_config_path


@dataclass(frozen=True, slots=True)
class FirstRunState:
    env_file_exists: bool
    project_config_exists: bool
    local_config_exists: bool

    @property
    def needs_setup(self) -> bool:
        return not self.local_config_exists

    @property
    def blocks_tui_launch(self) -> bool:
        return False


def inspect_first_run_state(*, project_root: Path, home: Path) -> FirstRunState:
    return FirstRunState(
        env_file_exists=(project_root / ".env").exists(),
        project_config_exists=(project_root / "awesome-agent.yaml").exists(),
        local_config_exists=user_config_path(home).exists(),
    )
