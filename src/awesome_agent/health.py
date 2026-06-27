from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class CheckSeverity(StrEnum):
    REQUIRED = "required"
    DEGRADED = "degraded"
    INFORMATIONAL = "informational"


class ReadinessProfile(StrEnum):
    API = "api"
    RUNTIME = "runtime"


@dataclass(frozen=True, slots=True)
class HealthCheck:
    name: str
    status: HealthStatus
    detail: str
    severity: CheckSeverity = CheckSeverity.REQUIRED
    remediation: str | None = None
    metadata: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status is not HealthStatus.UNHEALTHY

    @property
    def required(self) -> bool:
        return self.severity is CheckSeverity.REQUIRED


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    profile: ReadinessProfile
    status: HealthStatus
    checks: list[HealthCheck]
    generated_at: datetime


def _docker_health() -> HealthCheck:
    docker = shutil.which("docker")
    if docker is None:
        return HealthCheck(
            "docker",
            HealthStatus.UNHEALTHY,
            "Docker CLI was not found.",
            remediation="Install Docker Desktop or use --no-docker for local doctor.",
        )

    result = subprocess.run(
        [docker, "version", "--format", "{{.Server.Version}}"],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "Docker daemon is not reachable."
        return HealthCheck(
            "docker",
            HealthStatus.UNHEALTHY,
            detail,
            remediation="Start Docker Desktop before running sandboxed commands.",
        )
    return HealthCheck(
        "docker",
        HealthStatus.HEALTHY,
        f"server {result.stdout.strip()}",
    )


def collect_health(*, check_docker: bool = True) -> list[HealthCheck]:
    checks = [
        HealthCheck(
            "python",
            HealthStatus.HEALTHY
            if sys.version_info[:2] == (3, 12)
            else HealthStatus.UNHEALTHY,
            sys.version.split()[0],
        ),
        HealthCheck(
            "git",
            HealthStatus.HEALTHY
            if shutil.which("git") is not None
            else HealthStatus.UNHEALTHY,
            shutil.which("git") or "Git was not found.",
        ),
    ]
    if check_docker:
        checks.append(_docker_health())
    return checks


def is_healthy(checks: list[HealthCheck]) -> bool:
    return all(
        check.status is not HealthStatus.UNHEALTHY for check in checks if check.required
    )


def readiness_status(checks: list[HealthCheck]) -> HealthStatus:
    if any(
        check.required and check.status is HealthStatus.UNHEALTHY for check in checks
    ):
        return HealthStatus.UNHEALTHY
    if any(check.status is not HealthStatus.HEALTHY for check in checks):
        return HealthStatus.DEGRADED
    return HealthStatus.HEALTHY


def readiness_report(
    *,
    profile: ReadinessProfile,
    checks: list[HealthCheck],
) -> ReadinessReport:
    return ReadinessReport(
        profile=profile,
        status=readiness_status(checks),
        checks=checks,
        generated_at=datetime.now(UTC),
    )
