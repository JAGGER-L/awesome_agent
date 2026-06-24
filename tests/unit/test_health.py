from __future__ import annotations

import subprocess
from unittest.mock import patch

from awesome_agent.health import HealthCheck, _docker_health, collect_health, is_healthy


def test_is_healthy_accepts_passing_required_checks() -> None:
    checks = [
        HealthCheck("required", True, "ok"),
        HealthCheck("optional", False, "not configured", required=False),
    ]

    assert is_healthy(checks)


def test_is_healthy_rejects_failed_required_check() -> None:
    assert not is_healthy([HealthCheck("required", False, "failed")])


def test_collect_health_can_skip_docker() -> None:
    checks = collect_health(check_docker=False)

    assert [check.name for check in checks] == ["python", "git"]


def test_docker_health_reports_missing_cli() -> None:
    with patch("awesome_agent.health.shutil.which", return_value=None):
        check = _docker_health()

    assert not check.ok
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

    assert not check.ok
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

    assert check.ok
    assert check.detail == "server 29.2.1"
