import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from typer.testing import CliRunner

import awesome_agent.cli.app as cli_module
from awesome_agent.cli.app import app
from awesome_agent.domain.models import Repository

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


def test_run_registers_path_locally_and_sends_repository_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    repository = Repository(
        id=uuid4(),
        root=repository_path,
        display_name="repository",
        git_common_dir=repository_path / ".git",
    )
    request: dict[str, Any] = {}

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, str]:
            return {"id": "run-id"}

    def post(url: str, **kwargs: Any) -> Response:
        request["url"] = url
        request.update(kwargs)
        return Response()

    monkeypatch.setattr(
        cli_module,
        "_run_with_repository_service",
        lambda operation: repository,
    )
    monkeypatch.setattr(httpx, "post", post)

    result = runner.invoke(
        app,
        [
            "run",
            "Inspect code",
            "--repo",
            str(repository_path),
            "--read-only",
        ],
    )

    assert result.exit_code == 0
    assert request["json"] == {
        "repository_id": str(repository.id),
        "goal": "Inspect code",
        "intent": "read_only",
    }
