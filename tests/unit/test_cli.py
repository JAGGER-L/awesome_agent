import os
import sys
import types
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


def test_serve_rejects_public_bind_without_explicit_unsafe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_uvicorn = types.SimpleNamespace(run=lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    result = runner.invoke(app, ["serve", "--host", "0.0.0.0"])

    assert result.exit_code != 0
    assert "unauthenticated" in result.output


def test_serve_allows_public_bind_with_explicit_unsafe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def run(*args: Any, **kwargs: Any) -> None:
        calls.append({"args": args, "kwargs": kwargs})

    fake_uvicorn = types.SimpleNamespace(run=run)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    result = runner.invoke(
        app,
        ["serve", "--host", "0.0.0.0", "--unsafe-bind-public"],
    )

    assert result.exit_code == 0
    assert calls[0]["kwargs"]["host"] == "0.0.0.0"


def test_start_rejects_public_bind_without_explicit_unsafe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "run_supervisor",
        lambda **kwargs: pytest.fail("supervisor should not start"),
    )

    result = runner.invoke(app, ["start", "--host", "0.0.0.0"])

    assert result.exit_code != 0
    assert "unauthenticated" in result.output


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


def test_probe_sends_diagnostic_request(
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
            return {"id": "probe-id"}

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
        ["probe", "--repo", str(repository_path)],
    )

    assert result.exit_code == 0
    assert request["url"].endswith("/runtime/probes")
    assert request["json"] == {"repository_id": str(repository.id)}
