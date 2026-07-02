from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL_NAME = "deepseek-v4-pro"
DEFAULT_MODEL_API_KEY_ENV = "AWESOME_AGENT_DEEPSEEK_API_KEY"


@dataclass(frozen=True, slots=True)
class ConfigFlowSummary:
    home: Path
    project_root: Path
    user_config: Path
    project_config: Path
    project_env: Path
    user_config_exists: bool
    project_config_exists: bool
    project_env_exists: bool
    model_name: str
    model_api_key_env: str
    model_api_key_configured: bool

    @property
    def needs_model_setup(self) -> bool:
        return not self.model_api_key_configured


def user_config_path(home: Path) -> Path:
    return home / ".awesome-agent" / "config.yaml"


def create_default_user_config(home: Path) -> Path:
    path = user_config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(_default_config_text(), encoding="utf-8")
    return path


def inspect_config_flow(
    *,
    home: Path,
    project_root: Path,
    environ: Mapping[str, str],
) -> ConfigFlowSummary:
    user_config = user_config_path(home)
    project_config = project_root / "awesome-agent.yaml"
    project_env = project_root / ".env"
    return ConfigFlowSummary(
        home=home,
        project_root=project_root,
        user_config=user_config,
        project_config=project_config,
        project_env=project_env,
        user_config_exists=user_config.exists(),
        project_config_exists=project_config.exists(),
        project_env_exists=project_env.exists(),
        model_name=DEFAULT_MODEL_NAME,
        model_api_key_env=DEFAULT_MODEL_API_KEY_ENV,
        model_api_key_configured=bool(environ.get(DEFAULT_MODEL_API_KEY_ENV)),
    )


def _default_config_text() -> str:
    return "\n".join(
        [
            "version: 1",
            "models:",
            f"  default: {DEFAULT_MODEL_NAME}",
            "  profiles:",
            f"    - name: {DEFAULT_MODEL_NAME}",
            "      provider: deepseek",
            f"      model: {DEFAULT_MODEL_NAME}",
            f"      api_key_env: {DEFAULT_MODEL_API_KEY_ENV}",
            "sandbox:",
            "  local_cli_default: local",
            "  api_default: aio-docker",
            "",
        ]
    )
