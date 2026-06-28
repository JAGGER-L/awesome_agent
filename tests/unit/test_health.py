from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from pydantic import SecretStr

from awesome_agent.health import (
    CheckSeverity,
    HealthCheck,
    HealthStatus,
    ReadinessProfile,
    _docker_health,
    bind_policy_check,
    collect_health,
    is_healthy,
    model_routes_check,
    provider_key_check,
    readiness_status,
    workspace_root_check,
)
from awesome_agent.settings import Settings


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
    settings = Settings(deepseek_api_key=None)

    check = provider_key_check(settings, ReadinessProfile.API)

    assert check.status is HealthStatus.DEGRADED
    assert check.severity is CheckSeverity.DEGRADED


def test_provider_missing_is_unhealthy_for_runtime_profile() -> None:
    settings = Settings(deepseek_api_key=None)

    check = provider_key_check(settings, ReadinessProfile.RUNTIME)

    assert check.status is HealthStatus.UNHEALTHY
    assert check.severity is CheckSeverity.REQUIRED


def test_model_routes_check_reports_all_runtime_graph_identities() -> None:
    settings = Settings(deepseek_api_key=SecretStr("key"))

    check = model_routes_check(settings, ReadinessProfile.RUNTIME)

    assert check.status is HealthStatus.HEALTHY
    assert check.metadata == {
        "graph_identities": [
            "runtime-probe",
            "solo-readonly",
            "solo-modifying",
            "team-coding-scoped",
            "team-coding",
            "team-role",
            "team-verifier",
        ]
    }


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
