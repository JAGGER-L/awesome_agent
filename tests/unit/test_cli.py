import os
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from typer.testing import CliRunner

import awesome_agent.cli.app as cli_module
from awesome_agent.cli.app import app
from awesome_agent.domain.models import Repository
from awesome_agent.health import (
    CheckSeverity,
    HealthCheck,
    HealthStatus,
    ReadinessProfile,
    ReadinessReport,
)

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_doctor_can_skip_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    async def collect_readiness(*args: Any, **kwargs: Any) -> ReadinessReport:
        assert kwargs["check_docker"] is False
        return _readiness_report(
            HealthStatus.HEALTHY,
            [
                HealthCheck("python", HealthStatus.HEALTHY, "3.12"),
                HealthCheck("git", HealthStatus.HEALTHY, "git.exe"),
            ],
        )

    monkeypatch.setattr(cli_module, "collect_readiness", collect_readiness)

    result = runner.invoke(app, ["doctor", "--no-docker"])

    assert result.exit_code == 0
    assert "[PASS] python:" in result.stdout
    assert "[PASS] git:" in result.stdout


def test_doctor_degraded_exits_zero_and_prints_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def collect_readiness(*_: Any, **__: Any) -> ReadinessReport:
        return _readiness_report(
            HealthStatus.DEGRADED,
            [
                HealthCheck(
                    "provider",
                    HealthStatus.DEGRADED,
                    "DeepSeek API key is not configured",
                    severity=CheckSeverity.DEGRADED,
                    remediation="Set AWESOME_AGENT_DEEPSEEK_API_KEY.",
                )
            ],
        )

    monkeypatch.setattr(cli_module, "collect_readiness", collect_readiness)

    result = runner.invoke(app, ["doctor", "--profile", "api", "--no-docker"])

    assert result.exit_code == 0
    assert "[WARN] provider: DeepSeek API key is not configured" in result.stdout
    assert "fix: Set AWESOME_AGENT_DEEPSEEK_API_KEY." in result.stdout


def test_doctor_unhealthy_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    async def collect_readiness(*_: Any, **__: Any) -> ReadinessReport:
        return _readiness_report(
            HealthStatus.UNHEALTHY,
            [
                HealthCheck(
                    "worker_heartbeat",
                    HealthStatus.UNHEALTHY,
                    "no fresh worker heartbeat found",
                )
            ],
        )

    monkeypatch.setattr(cli_module, "collect_readiness", collect_readiness)

    result = runner.invoke(app, ["doctor", "--profile", "runtime", "--no-docker"])

    assert result.exit_code == 1
    assert "[FAIL] worker_heartbeat: no fresh worker heartbeat found" in result.stdout


def test_doctor_runtime_includes_worker_and_provider_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    async def collect_readiness(
        settings: object,
        profile: ReadinessProfile,
        **kwargs: Any,
    ) -> ReadinessReport:
        seen["profile"] = profile
        seen["worker_heartbeat_repository"] = kwargs.get("worker_heartbeat_repository")
        return _readiness_report(
            HealthStatus.UNHEALTHY,
            [
                HealthCheck("provider", HealthStatus.HEALTHY, "configured"),
                HealthCheck(
                    "worker_heartbeat",
                    HealthStatus.UNHEALTHY,
                    "no fresh worker heartbeat found",
                ),
            ],
            profile=profile,
        )

    monkeypatch.setattr(cli_module, "collect_readiness", collect_readiness)

    result = runner.invoke(app, ["doctor", "--profile", "runtime", "--no-docker"])

    assert result.exit_code == 1
    assert seen["profile"] is ReadinessProfile.RUNTIME
    assert seen["worker_heartbeat_repository"] is not None
    assert "[PASS] provider:" in result.stdout
    assert "[FAIL] worker_heartbeat:" in result.stdout


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
    assert os.environ["AWESOME_AGENT_API_HOST"] == "0.0.0.0"
    assert os.environ["AWESOME_AGENT_UNSAFE_BIND_PUBLIC"] == "true"


def test_start_sets_api_bind_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def run_supervisor(**kwargs: Any) -> types.SimpleNamespace:
        calls.append(kwargs)
        return types.SimpleNamespace(return_code=0, service="api")

    monkeypatch.setattr(cli_module, "run_supervisor", run_supervisor)

    result = runner.invoke(
        app,
        ["start", "--host", "127.0.0.1"],
    )

    assert result.exit_code == 0
    assert calls[0]["host"] == "127.0.0.1"
    assert os.environ["AWESOME_AGENT_API_HOST"] == "127.0.0.1"
    assert os.environ["AWESOME_AGENT_UNSAFE_BIND_PUBLIC"] == "false"


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
        "mode": "solo",
    }


def test_run_can_request_team_mode(
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
            return {"id": "team-run-id"}

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
            "Implement backend and verify it",
            "--repo",
            str(repository_path),
            "--team",
        ],
    )

    assert result.exit_code == 0
    assert request["json"] == {
        "repository_id": str(repository.id),
        "goal": "Implement backend and verify it",
        "intent": "modifying",
        "mode": "team",
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


def test_workspace_list_reads_api(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> list[dict[str, Any]]:
            return [
                {
                    "run_id": str(uuid4()),
                    "status": "eligible",
                    "can_cleanup": True,
                    "dirty": False,
                    "reason": "workspace is eligible for cleanup",
                }
            ]

    def get(url: str, **kwargs: Any) -> Response:
        calls.append(url)
        return Response()

    monkeypatch.setattr(httpx, "get", get)

    result = runner.invoke(app, ["workspace", "list"])

    assert result.exit_code == 0
    assert calls[0].endswith("/workspaces")
    assert "eligible" in result.stdout


def test_workspace_cleanup_defaults_to_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = uuid4()
    request: dict[str, Any] = {}

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> list[dict[str, Any]]:
            return []

    def post(url: str, **kwargs: Any) -> Response:
        request["url"] = url
        request.update(kwargs)
        return Response()

    monkeypatch.setattr(httpx, "post", post)

    result = runner.invoke(app, ["workspace", "cleanup", "--run-id", str(run_id)])

    assert result.exit_code == 0
    assert request["url"].endswith("/workspaces/cleanup-preview")
    assert request["json"] == {
        "run_id": str(run_id),
        "older_than": None,
        "force": False,
        "reason": None,
    }


def test_workspace_cleanup_apply_uses_cleanup_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request: dict[str, Any] = {}

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> list[dict[str, Any]]:
            return []

    def post(url: str, **kwargs: Any) -> Response:
        request["url"] = url
        request.update(kwargs)
        return Response()

    monkeypatch.setattr(httpx, "post", post)

    result = runner.invoke(
        app,
        ["workspace", "cleanup", "--older-than", "14d", "--apply"],
    )

    assert result.exit_code == 0
    assert request["url"].endswith("/workspaces/cleanup")
    assert request["json"]["older_than"] == "14d"


def test_workspace_cleanup_force_requires_reason() -> None:
    result = runner.invoke(app, ["workspace", "cleanup", "--force"])

    assert result.exit_code != 0
    assert "reason" in result.output


def _readiness_report(
    status: HealthStatus,
    checks: list[HealthCheck],
    *,
    profile: ReadinessProfile = ReadinessProfile.API,
) -> ReadinessReport:
    return ReadinessReport(
        profile=profile,
        status=status,
        checks=checks,
        generated_at=datetime.now(UTC),
    )
