from pathlib import Path

from awesome_agent.cli.config_flow import (
    create_default_user_config,
    inspect_config_flow,
    user_config_path,
)


def test_user_config_path_uses_awesome_home(tmp_path: Path) -> None:
    assert user_config_path(tmp_path) == tmp_path / ".awesome-agent" / "config.yaml"


def test_create_default_user_config_does_not_write_secret(tmp_path: Path) -> None:
    path = create_default_user_config(tmp_path)

    content = path.read_text(encoding="utf-8")
    assert "AWESOME_AGENT_DEEPSEEK_API_KEY" in content
    assert "your-api-key" not in content
    assert path == tmp_path / ".awesome-agent" / "config.yaml"


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
