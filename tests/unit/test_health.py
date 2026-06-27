from __future__ import annotations

import subprocess
from unittest.mock import patch

from awesome_agent.health import (
    CheckSeverity,
    HealthCheck,
    HealthStatus,
    _docker_health,
    collect_health,
    is_healthy,
    readiness_status,
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
    assert not is_healthy(
        [HealthCheck("required", HealthStatus.UNHEALTHY, "failed")]
    )


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
