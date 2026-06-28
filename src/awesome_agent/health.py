from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from ipaddress import ip_address
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from awesome_agent.persistence.checkpoints import checkpoint_saver
from awesome_agent.repositories.config import LocalRepositoryConfigStore
from awesome_agent.runtime.graphs import (
    MODIFYING_CODING_ROUTE,
    READ_ONLY_CODING_ROUTE,
    RUNTIME_PROBE_ROUTE,
    SCOPED_TEAM_CODING_ROUTE,
    TEAM_CODING_ROUTE,
    TEAM_ROLE_ROUTE,
    TEAM_VERIFIER_ROUTE,
)
from awesome_agent.settings import Settings


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


def python_check() -> HealthCheck:
    return HealthCheck(
        "python",
        HealthStatus.HEALTHY
        if sys.version_info[:2] == (3, 12)
        else HealthStatus.UNHEALTHY,
        sys.version.split()[0],
        remediation="Run awesome_agent with Python 3.12.",
    )


def git_check() -> HealthCheck:
    git = shutil.which("git")
    return HealthCheck(
        "git",
        HealthStatus.HEALTHY if git is not None else HealthStatus.UNHEALTHY,
        git or "Git was not found.",
        remediation="Install Git and ensure it is available on PATH.",
    )


def docker_check() -> HealthCheck:
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


def _docker_health() -> HealthCheck:
    return docker_check()


async def database_check(database_url: str) -> HealthCheck:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as error:
        return HealthCheck(
            "database",
            HealthStatus.UNHEALTHY,
            f"database check failed: {error}",
            remediation="Start PostgreSQL and verify AWESOME_AGENT_DATABASE_URL.",
        )
    finally:
        await engine.dispose()
    return HealthCheck("database", HealthStatus.HEALTHY, "connected")


async def migration_check(database_url: str) -> HealthCheck:
    engine = create_async_engine(database_url)
    try:
        root = _project_root()
        config = Config(str(root / "alembic.ini"))
        script = ScriptDirectory.from_config(config)
        expected_heads = set(script.get_heads())
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT version_num FROM alembic_version")
            )
            current_versions = {str(row[0]) for row in result.fetchall()}
    except Exception as error:
        return HealthCheck(
            "migration",
            HealthStatus.UNHEALTHY,
            f"migration check failed: {error}",
            remediation="Run scripts\\migrate.ps1 and verify database access.",
        )
    finally:
        await engine.dispose()

    if current_versions == expected_heads:
        return HealthCheck(
            "migration",
            HealthStatus.HEALTHY,
            f"at revision {', '.join(sorted(current_versions))}",
        )
    return HealthCheck(
        "migration",
        HealthStatus.UNHEALTHY,
        "database revision "
        f"{', '.join(sorted(current_versions)) or '<none>'} does not match "
        f"script head {', '.join(sorted(expected_heads))}",
        remediation="Run scripts\\migrate.ps1 before starting runtime services.",
        metadata={
            "current_versions": sorted(current_versions),
            "expected_heads": sorted(expected_heads),
        },
    )


async def checkpoint_check(checkpoint_database_url: str) -> HealthCheck:
    try:
        async with checkpoint_saver(checkpoint_database_url) as saver:
            await saver.setup()
    except Exception as error:
        return HealthCheck(
            "checkpoint",
            HealthStatus.UNHEALTHY,
            f"checkpoint check failed: {error}",
            remediation="Verify AWESOME_AGENT_CHECKPOINT_DATABASE_URL.",
        )
    return HealthCheck("checkpoint", HealthStatus.HEALTHY, "checkpoint store ready")


def workspace_root_check(path: Path) -> HealthCheck:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".awesome-agent-healthcheck"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as error:
        return HealthCheck(
            "workspace_root",
            HealthStatus.UNHEALTHY,
            f"workspace root {path}: {error}",
            remediation="Choose a writable workspace root in local config.",
        )
    return HealthCheck("workspace_root", HealthStatus.HEALTHY, str(path))


def provider_key_check(settings: Settings, profile: ReadinessProfile) -> HealthCheck:
    key = settings.deepseek_api_key
    has_key = bool(key and key.get_secret_value())
    if has_key:
        return HealthCheck(
            "provider",
            HealthStatus.HEALTHY,
            "DeepSeek API key configured",
        )
    if profile is ReadinessProfile.RUNTIME:
        return HealthCheck(
            "provider",
            HealthStatus.UNHEALTHY,
            "DeepSeek API key is not configured",
            severity=CheckSeverity.REQUIRED,
            remediation="Set AWESOME_AGENT_DEEPSEEK_API_KEY.",
        )
    return HealthCheck(
        "provider",
        HealthStatus.DEGRADED,
        "DeepSeek API key is not configured",
        severity=CheckSeverity.DEGRADED,
        remediation="Set AWESOME_AGENT_DEEPSEEK_API_KEY before running agents.",
    )


def model_routes_check(settings: Settings, profile: ReadinessProfile) -> HealthCheck:
    graph_identities = _graph_identities()
    configured_models = [
        settings.leader_model,
        settings.teammate_model,
        settings.verifier_model,
        settings.subagent_model,
    ]
    missing_models = [model for model in configured_models if not model]
    if missing_models:
        return HealthCheck(
            "model_routes",
            HealthStatus.UNHEALTHY,
            "one or more role models are not configured",
            remediation="Set role model names in environment or config.",
            metadata={"graph_identities": graph_identities},
        )
    return HealthCheck(
        "model_routes",
        HealthStatus.HEALTHY,
        f"{profile.value} runtime routes configured",
        metadata={"graph_identities": graph_identities},
    )


def bind_policy_check(host: str, unsafe_bind_public: bool) -> HealthCheck:
    if _is_loopback_host(host):
        return HealthCheck("api_bind", HealthStatus.HEALTHY, f"loopback host {host}")
    if unsafe_bind_public:
        return HealthCheck(
            "api_bind",
            HealthStatus.DEGRADED,
            f"non-loopback host {host} allowed by explicit unsafe consent",
            severity=CheckSeverity.DEGRADED,
            remediation=(
                "Use loopback binding unless exposing the local API is intended."
            ),
        )
    return HealthCheck(
        "api_bind",
        HealthStatus.UNHEALTHY,
        f"non-loopback host {host} requires explicit unsafe consent",
        remediation=(
            "Use --unsafe-bind-public only when local API exposure is intended."
        ),
    )


def resolve_workspace_root(settings: Settings) -> Path:
    if settings.workspace_root is not None:
        return settings.workspace_root
    return LocalRepositoryConfigStore(settings.local_config_path).load().workspace_root


async def collect_readiness(
    settings: Settings,
    profile: ReadinessProfile,
    *,
    check_docker: bool = True,
    worker_heartbeat_repository: Any | None = None,
) -> ReadinessReport:
    checks = collect_health(check_docker=check_docker)
    checks.extend(
        [
            bind_policy_check(settings.api_host, settings.unsafe_bind_public),
            workspace_root_check(resolve_workspace_root(settings)),
            provider_key_check(settings, profile),
            model_routes_check(settings, profile),
            await database_check(settings.database_url),
            await migration_check(settings.database_url),
            await checkpoint_check(settings.checkpoint_database_url),
        ]
    )
    if profile is ReadinessProfile.RUNTIME:
        checks.append(
            await _runtime_worker_heartbeat_check(
                settings,
                worker_heartbeat_repository=worker_heartbeat_repository,
            )
        )
    return readiness_report(profile=profile, checks=checks)


def collect_health(*, check_docker: bool = True) -> list[HealthCheck]:
    checks = [python_check(), git_check()]
    if check_docker:
        checks.append(docker_check())
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


def _graph_identities() -> list[str]:
    return [
        RUNTIME_PROBE_ROUTE,
        READ_ONLY_CODING_ROUTE,
        MODIFYING_CODING_ROUTE,
        SCOPED_TEAM_CODING_ROUTE,
        TEAM_CODING_ROUTE,
        TEAM_ROLE_ROUTE,
        TEAM_VERIFIER_ROUTE,
    ]


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _project_root() -> Path:
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "alembic.ini").exists():
        return candidate
    return Path.cwd()


async def _runtime_worker_heartbeat_check(
    settings: Settings,
    *,
    worker_heartbeat_repository: Any | None,
) -> HealthCheck:
    if worker_heartbeat_repository is None:
        return HealthCheck(
            "worker_heartbeat",
            HealthStatus.UNHEALTHY,
            "worker heartbeat repository is not configured",
            remediation="Start the API with PostgreSQL persistence configured.",
        )

    from awesome_agent.runtime.worker_heartbeats import (
        RuntimeRoute,
        worker_heartbeat_check,
    )

    return await worker_heartbeat_check(
        worker_heartbeat_repository,
        settings,
        required_runtime_routes=[
            RuntimeRoute(RUNTIME_PROBE_ROUTE),
            RuntimeRoute(READ_ONLY_CODING_ROUTE),
            RuntimeRoute(MODIFYING_CODING_ROUTE),
            RuntimeRoute(SCOPED_TEAM_CODING_ROUTE),
            RuntimeRoute(TEAM_CODING_ROUTE),
            RuntimeRoute(TEAM_ROLE_ROUTE),
            RuntimeRoute(TEAM_VERIFIER_ROUTE),
        ],
    )
