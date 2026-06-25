import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from awesome_agent.cli.app import app

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_doctor_can_skip_docker() -> None:
    result = runner.invoke(app, ["doctor", "--no-docker"])

    assert result.exit_code == 0
    assert "[PASS] python:" in result.stdout
    assert "[PASS] git:" in result.stdout


def test_config_root_add_and_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setenv("AWESOME_AGENT_LOCAL_CONFIG_PATH", str(config_path))

    added = runner.invoke(app, ["config", "root", "add", str(projects)])
    listed = runner.invoke(app, ["config", "root", "list"])

    assert added.exit_code == 0
    assert listed.exit_code == 0
    assert os.path.normcase(str(projects.resolve())) in listed.stdout
