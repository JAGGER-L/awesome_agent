from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HealthCheck:
    name: str
    ok: bool
    detail: str
    required: bool = True


def _docker_health() -> HealthCheck:
    docker = shutil.which("docker")
    if docker is None:
        return HealthCheck("docker", False, "Docker CLI was not found.")

    result = subprocess.run(
        [docker, "version", "--format", "{{.Server.Version}}"],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "Docker daemon is not reachable."
        return HealthCheck("docker", False, detail)
    return HealthCheck("docker", True, f"server {result.stdout.strip()}")


def collect_health(*, check_docker: bool = True) -> list[HealthCheck]:
    checks = [
        HealthCheck(
            "python",
            sys.version_info[:2] == (3, 12),
            sys.version.split()[0],
        ),
        HealthCheck(
            "git",
            shutil.which("git") is not None,
            shutil.which("git") or "Git was not found.",
        ),
    ]
    if check_docker:
        checks.append(_docker_health())
    return checks


def is_healthy(checks: list[HealthCheck]) -> bool:
    return all(check.ok for check in checks if check.required)
