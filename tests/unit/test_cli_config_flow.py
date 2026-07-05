from pathlib import Path

from awesome_agent.cli.config_flow import (
    create_default_user_config,
    initialize_user_config,
    inspect_config_flow,
    user_config_path,
    user_env_path,
)
from awesome_agent.paths import AwesomePaths


def test_user_config_path_uses_awesome_home(tmp_path: Path) -> None:
    assert user_config_path(tmp_path) == tmp_path / "config.yaml"
    assert user_env_path(tmp_path) == tmp_path / ".env"


def test_create_default_user_config_does_not_write_secret(tmp_path: Path) -> None:
    path = create_default_user_config(tmp_path)

    content = path.read_text(encoding="utf-8")
    assert "AWESOME_AGENT_DEEPSEEK_API_KEY" in content
    assert "your-api-key" not in content
    assert path == tmp_path / "config.yaml"


def test_initialize_user_config_creates_runtime_skeleton_without_overwriting_env(
    tmp_path: Path,
) -> None:
    paths = AwesomePaths.from_home(tmp_path / "awesome-home")
    paths.env_file.parent.mkdir(parents=True)
    paths.env_file.write_text(
        "AWESOME_AGENT_DEEPSEEK_API_KEY=existing-key\n",
        encoding="utf-8",
    )

    summary = initialize_user_config(paths)

    assert summary.home == paths.home
    assert summary.config_file == paths.config_file
    assert summary.env_file == paths.env_file
    assert summary.user_extension_config == paths.user_extension_config
    assert paths.config_file.exists()
    assert paths.user_extension_config.exists()
    assert paths.env_file.read_text(encoding="utf-8") == (
        "AWESOME_AGENT_DEEPSEEK_API_KEY=existing-key\n"
    )
    for directory in [
        paths.skills_dir,
        paths.state_dir,
        paths.runs_dir,
        paths.logs_dir,
    ]:
        assert directory.is_dir()


def test_inspect_config_flow_reports_missing_api_key(tmp_path: Path) -> None:
    create_default_user_config(tmp_path)

    summary = inspect_config_flow(
        home=tmp_path,
        project_root=tmp_path / "project",
        environ={},
    )

    assert summary.user_config_exists is True
    assert summary.model_api_key_env == "AWESOME_AGENT_DEEPSEEK_API_KEY"
    assert summary.model_api_key_configured is False
    assert summary.model_api_key_source is None
    assert summary.awesome_env == tmp_path / ".env"


def test_inspect_config_flow_reports_settings_api_key_source(tmp_path: Path) -> None:
    create_default_user_config(tmp_path)

    summary = inspect_config_flow(
        home=tmp_path,
        project_root=tmp_path / "project",
        environ={},
        settings_api_key_configured=True,
    )

    assert summary.model_api_key_configured is True
    assert summary.model_api_key_source == "awesome_env"


def test_inspect_config_flow_prefers_process_env_source(tmp_path: Path) -> None:
    create_default_user_config(tmp_path)

    summary = inspect_config_flow(
        home=tmp_path,
        project_root=tmp_path / "project",
        environ={"AWESOME_AGENT_DEEPSEEK_API_KEY": "test-key"},
        settings_api_key_configured=True,
    )

    assert summary.model_api_key_configured is True
    assert summary.model_api_key_source == "environment"
