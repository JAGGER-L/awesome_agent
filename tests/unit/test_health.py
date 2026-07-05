from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from tests.type_helpers import test_settings

from awesome_agent.health import (
    CheckSeverity,
    HealthCheck,
    HealthStatus,
    ReadinessProfile,
    _docker_health,
    _runtime_worker_heartbeat_check,
    aio_sandbox_check,
    bind_policy_check,
    collect_health,
    is_healthy,
    model_routes_check,
    provider_key_check,
    readiness_status,
    workspace_root_check,
)
from awesome_agent.runtime.graphs import (
    MODIFYING_CODING_ROUTE,
    READ_ONLY_CODING_ROUTE,
    RUNTIME_PROBE_ROUTE,
    SCOPED_TEAM_CODING_ROUTE,
    TEAM_CODING_ROUTE,
    TEAM_ROLE_ROUTE,
    TEAM_VERIFIER_ROUTE,
)
from awesome_agent.runtime.worker_heartbeats import (
    InMemoryWorkerHeartbeatRepository,
    RuntimeRoute,
    WorkerHeartbeat,
    WorkerHeartbeatStatus,
)


def test_is_healthy_accepts_passing_required_checks() -> None:
    checks = [
        HealthCheck(
            "required",
            HealthStatus.HEALTHY,
            "ok",
            severity=CheckSeverity.REQUIRED,
        ),
        HealthCheck(
            "optional",
            HealthStatus.UNHEALTHY,
            "not configured",
            severity=CheckSeverity.INFORMATIONAL,
        ),
    ]

    assert is_healthy(checks)


def test_is_healthy_rejects_failed_required_check() -> None:
    assert not is_healthy([HealthCheck("required", HealthStatus.UNHEALTHY, "failed")])


def test_readiness_status_reports_healthy_when_all_required_pass() -> None:
    checks = [
        HealthCheck(
            name="database",
            status=HealthStatus.HEALTHY,
            detail="connected",
            severity=CheckSeverity.REQUIRED,
            remediation=None,
        )
    ]

    assert readiness_status(checks) is HealthStatus.HEALTHY


def test_readiness_status_reports_degraded_for_degraded_checks() -> None:
    checks = [
        HealthCheck(
            name="provider",
            status=HealthStatus.DEGRADED,
            detail="DeepSeek key is not configured",
            severity=CheckSeverity.DEGRADED,
            remediation="Set AWESOME_AGENT_DEEPSEEK_API_KEY.",
        )
    ]

    assert readiness_status(checks) is HealthStatus.DEGRADED


def test_readiness_status_reports_unhealthy_for_required_failure() -> None:
    checks = [
        HealthCheck(
            name="database",
            status=HealthStatus.UNHEALTHY,
            detail="connection refused",
            severity=CheckSeverity.REQUIRED,
            remediation="Start PostgreSQL with docker compose up -d postgres.",
        )
    ]

    assert readiness_status(checks) is HealthStatus.UNHEALTHY


def test_collect_health_can_skip_docker() -> None:
    checks = collect_health(check_docker=False)

    assert [check.name for check in checks] == ["python", "git"]


def test_docker_health_reports_missing_cli() -> None:
    with patch("awesome_agent.health.shutil.which", return_value=None):
        check = _docker_health()

    assert check.status is HealthStatus.UNHEALTHY
    assert check.detail == "Docker CLI was not found."


def test_docker_health_reports_unreachable_daemon() -> None:
    completed = subprocess.CompletedProcess(
        args=["docker"],
        returncode=1,
        stdout="",
        stderr="daemon unavailable",
    )
    with (
        patch("awesome_agent.health.shutil.which", return_value="docker.exe"),
        patch(
            "awesome_agent.health.subprocess.run",
            return_value=completed,
        ),
    ):
        check = _docker_health()

    assert check.status is HealthStatus.UNHEALTHY
    assert check.detail == "daemon unavailable"


def test_docker_health_reports_server_version() -> None:
    completed = subprocess.CompletedProcess(
        args=["docker"],
        returncode=0,
        stdout="29.2.1\n",
        stderr="",
    )
    with (
        patch("awesome_agent.health.shutil.which", return_value="docker.exe"),
        patch(
            "awesome_agent.health.subprocess.run",
            return_value=completed,
        ),
    ):
        check = _docker_health()

    assert check.status is HealthStatus.HEALTHY
    assert check.detail == "server 29.2.1"


def test_provider_missing_is_degraded_for_api_profile() -> None:
    settings = test_settings(deepseek_api_key=None)

    check = provider_key_check(settings, ReadinessProfile.API)

    assert check.status is HealthStatus.DEGRADED
    assert check.severity is CheckSeverity.DEGRADED


def test_provider_missing_is_unhealthy_for_runtime_profile() -> None:
    settings = test_settings(deepseek_api_key=None)

    check = provider_key_check(settings, ReadinessProfile.RUNTIME)

    assert check.status is HealthStatus.UNHEALTHY
    assert check.severity is CheckSeverity.REQUIRED


def test_provider_custom_base_url_is_unhealthy() -> None:
    settings = test_settings(
        deepseek_api_key="key",
        deepseek_base_url="https://gateway.local/v1",
    )

    check = provider_key_check(settings, ReadinessProfile.API)

    assert check.status is HealthStatus.UNHEALTHY
    assert check.metadata == {"code": "unsupported_provider_configuration"}
    assert check.remediation == (
        "Use the official DeepSeek endpoint: https://api.deepseek.com."
    )


def test_provider_readiness_ignores_openai_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWESOME_AGENT_OPENAI_API_KEY", "openai-secret")
    settings = test_settings(deepseek_api_key=None)

    check = provider_key_check(settings, ReadinessProfile.API)

    assert check.status is HealthStatus.DEGRADED
    assert "OpenAI" not in check.detail
    assert "openai" not in str(check.metadata).casefold()


def test_model_routes_check_reports_all_runtime_graph_identities() -> None:
    settings = test_settings(deepseek_api_key=SecretStr("key"))

    check = model_routes_check(settings, ReadinessProfile.RUNTIME)

    assert check.status is HealthStatus.HEALTHY
    assert check.metadata == {
        "graph_identities": [
            "runtime-probe",
            "solo-readonly",
            "solo-modifying",
            "team-coding",
            "team-role",
            "team-verifier",
        ],
        "role_models": {
            "leader": "deepseek-v4-pro",
            "teammate": "deepseek-v4-flash",
            "verifier": "deepseek-v4-flash",
            "subagent": "deepseek-v4-flash",
        },
    }


async def test_runtime_readiness_requires_distributed_team_routes_only() -> None:
    settings = test_settings(deepseek_api_key=SecretStr("key"))
    heartbeats = InMemoryWorkerHeartbeatRepository()
    await heartbeats.upsert(
        WorkerHeartbeat(
            worker_id=uuid4(),
            worker_name="worker",
            started_at=datetime.now(UTC),
            heartbeat_at=datetime.now(UTC),
            supported_runtime_routes=[
                RuntimeRoute(RUNTIME_PROBE_ROUTE),
                RuntimeRoute(READ_ONLY_CODING_ROUTE),
                RuntimeRoute(MODIFYING_CODING_ROUTE),
                RuntimeRoute(TEAM_CODING_ROUTE),
                RuntimeRoute(TEAM_ROLE_ROUTE),
                RuntimeRoute(TEAM_VERIFIER_ROUTE),
            ],
            status=WorkerHeartbeatStatus.ONLINE,
        )
    )

    check = await _runtime_worker_heartbeat_check(
        settings,
        worker_heartbeat_repository=heartbeats,
    )

    assert check.status is HealthStatus.HEALTHY
    assert check.metadata is not None
    assert SCOPED_TEAM_CODING_ROUTE not in check.metadata["required_runtime_routes"]


def test_model_routes_check_rejects_invalid_role_model() -> None:
    settings = test_settings(
        deepseek_api_key=SecretStr("key"),
        leader_model="gpt-4o",
    )

    check = model_routes_check(settings, ReadinessProfile.RUNTIME)

    assert check.status is HealthStatus.UNHEALTHY
    assert check.metadata is not None
    assert check.metadata["code"] == "invalid_role_model"
    assert "leader" in check.detail


def test_workspace_root_is_healthy_when_it_exists_and_is_writable(
    tmp_path: Path,
) -> None:
    check = workspace_root_check(tmp_path / "workspaces")

    assert check.status is HealthStatus.HEALTHY
    assert (tmp_path / "workspaces").is_dir()


def test_workspace_root_is_unhealthy_when_probe_file_cannot_be_written(
    tmp_path: Path,
) -> None:
    file_parent = tmp_path / "not-a-directory"
    file_parent.write_text("fixture", encoding="utf-8")

    check = workspace_root_check(file_parent / "workspaces")

    assert check.status is HealthStatus.UNHEALTHY
    assert "workspace root" in check.detail


def test_bind_policy_is_unhealthy_for_public_bind_without_unsafe_consent() -> None:
    check = bind_policy_check("0.0.0.0", unsafe_bind_public=False)

    assert check.status is HealthStatus.UNHEALTHY
    assert check.severity is CheckSeverity.REQUIRED


def test_bind_policy_accepts_loopback_without_unsafe_consent() -> None:
    check = bind_policy_check("127.0.0.1", unsafe_bind_public=False)

    assert check.status is HealthStatus.HEALTHY


async def test_aio_sandbox_check_reports_healthy() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "healthy"})

    check = await aio_sandbox_check(
        "http://sandbox:8765",
        transport=httpx.MockTransport(handler),
    )

    assert check.name == "aio_sandbox"
    assert check.status is HealthStatus.HEALTHY


async def test_aio_sandbox_check_reports_unreachable() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    check = await aio_sandbox_check(
        "http://sandbox:8765",
        transport=httpx.MockTransport(handler),
    )

    assert check.name == "aio_sandbox"
    assert check.status is HealthStatus.UNHEALTHY
    assert "connection refused" in check.detail
