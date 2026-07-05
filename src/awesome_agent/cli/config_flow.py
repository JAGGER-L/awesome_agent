from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from awesome_agent.paths import AwesomePaths

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
    model_api_key_source: str | None = None
    awesome_env: Path | None = None
    awesome_env_exists: bool | None = None

    @property
    def needs_model_setup(self) -> bool:
        return not self.model_api_key_configured


@dataclass(frozen=True, slots=True)
class UserConfigInitSummary:
    home: Path
    config_file: Path
    env_file: Path
    user_extension_config: Path
    skills_dir: Path
    state_dir: Path
    runs_dir: Path
    logs_dir: Path


def user_config_path(home: Path) -> Path:
    return home / "config.yaml"


def user_env_path(home: Path) -> Path:
    return home / ".env"


def create_default_user_config(home: Path) -> Path:
    paths = AwesomePaths.from_home(home)
    return initialize_user_config(paths).config_file


def initialize_user_config(paths: AwesomePaths) -> UserConfigInitSummary:
    paths.home.mkdir(parents=True, exist_ok=True)
    for directory in [
        paths.skills_dir,
        paths.state_dir,
        paths.runs_dir,
        paths.logs_dir,
    ]:
        directory.mkdir(parents=True, exist_ok=True)
    _write_if_missing(paths.config_file, _default_config_text())
    _write_if_missing(paths.env_file, _default_env_text())
    _write_if_missing(
        paths.user_extension_config,
        _default_user_extension_config_text(),
    )
    return UserConfigInitSummary(
        home=paths.home,
        config_file=paths.config_file,
        env_file=paths.env_file,
        user_extension_config=paths.user_extension_config,
        skills_dir=paths.skills_dir,
        state_dir=paths.state_dir,
        runs_dir=paths.runs_dir,
        logs_dir=paths.logs_dir,
    )


def inspect_config_flow(
    *,
    home: Path,
    project_root: Path,
    environ: Mapping[str, str],
    settings_api_key_configured: bool = False,
) -> ConfigFlowSummary:
    user_config = user_config_path(home)
    awesome_env = user_env_path(home)
    project_config = project_root / "awesome-agent.yaml"
    project_env = project_root / ".env"
    env_api_key_configured = bool(environ.get(DEFAULT_MODEL_API_KEY_ENV))
    model_api_key_source = (
        "environment"
        if env_api_key_configured
        else "awesome_env"
        if settings_api_key_configured
        else None
    )
    return ConfigFlowSummary(
        home=home,
        project_root=project_root,
        user_config=user_config,
        project_config=project_config,
        project_env=project_env,
        awesome_env=awesome_env,
        user_config_exists=user_config.exists(),
        project_config_exists=project_config.exists(),
        project_env_exists=project_env.exists(),
        awesome_env_exists=awesome_env.exists(),
        model_name=DEFAULT_MODEL_NAME,
        model_api_key_env=DEFAULT_MODEL_API_KEY_ENV,
        model_api_key_configured=env_api_key_configured or settings_api_key_configured,
        model_api_key_source=model_api_key_source,
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


def _default_env_text() -> str:
    return "\n".join(
        [
            "# Awesome Agent user environment.",
            "# Set provider secrets in the OS environment or below.",
            f"# {DEFAULT_MODEL_API_KEY_ENV}=",
            "",
        ]
    )


def _default_user_extension_config_text() -> str:
    return "\n".join(
        [
            "version: 1",
            "extensions:",
            "  skills:",
            "    auto_discover_project_skills: true",
            "    roots: []",
            "  sources: []",
            "",
        ]
    )


def _write_if_missing(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(text, encoding="utf-8")
