from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.health import (
    HealthCheck,
    HealthStatus,
    ReadinessProfile,
    ReadinessReport,
    checkpoint_check,
    collect_readiness,
    database_check,
    migration_check,
)
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.models import WorkerHeartbeatRecord
from awesome_agent.persistence.worker_heartbeats import (
    PostgresWorkerHeartbeatRepository,
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
    RuntimeRoute,
    WorkerHeartbeat,
    WorkerHeartbeatStatus,
)
from awesome_agent.settings import Settings

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_database_and_migration_readiness_are_healthy() -> None:
    database_url = os.environ["AWESOME_AGENT_TEST_DATABASE_URL"]

    database = await database_check(database_url)
    migration = await migration_check(database_url)

    assert database.status is HealthStatus.HEALTHY
    assert migration.status is HealthStatus.HEALTHY


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL" not in os.environ,
    reason="Checkpoint database is not configured.",
)
async def test_checkpoint_readiness_is_healthy() -> None:
    checkpoint = await checkpoint_check(
        os.environ["AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL"]
    )

    assert checkpoint.status is HealthStatus.HEALTHY


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ
    or "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL" not in os.environ,
    reason="Runtime and checkpoint databases are not configured.",
)
async def test_runtime_readiness_is_unhealthy_without_fresh_worker_heartbeat(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, deepseek_api_key=SecretStr("key"))
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    await _clear_worker_heartbeats(sessions)
    repository = PostgresWorkerHeartbeatRepository(sessions)

    report = await collect_readiness(
        settings,
        ReadinessProfile.RUNTIME,
        check_docker=False,
        worker_heartbeat_repository=repository,
    )

    assert report.status is HealthStatus.UNHEALTHY
    assert _check(report, "worker_heartbeat").status is HealthStatus.UNHEALTHY
    await engine.dispose()


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ
    or "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL" not in os.environ,
    reason="Runtime and checkpoint databases are not configured.",
)
async def test_runtime_readiness_is_healthy_with_fresh_worker_heartbeat(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, deepseek_api_key=SecretStr("key"))
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    await _clear_worker_heartbeats(sessions)
    repository = PostgresWorkerHeartbeatRepository(sessions)
    now = datetime.now(UTC)
    await repository.upsert(
        WorkerHeartbeat(
            worker_id=uuid4(),
            worker_name="worker-a",
            started_at=now,
            heartbeat_at=now,
            supported_runtime_routes=[
                RuntimeRoute(RUNTIME_PROBE_ROUTE),
                RuntimeRoute(READ_ONLY_CODING_ROUTE),
                RuntimeRoute(MODIFYING_CODING_ROUTE),
                RuntimeRoute(SCOPED_TEAM_CODING_ROUTE),
                RuntimeRoute(TEAM_CODING_ROUTE),
                RuntimeRoute(TEAM_ROLE_ROUTE),
                RuntimeRoute(TEAM_VERIFIER_ROUTE),
            ],
            status=WorkerHeartbeatStatus.ONLINE,
        )
    )

    report = await collect_readiness(
        settings,
        ReadinessProfile.RUNTIME,
        check_docker=False,
        worker_heartbeat_repository=repository,
    )

    assert report.status is HealthStatus.HEALTHY
    assert _check(report, "worker_heartbeat").status is HealthStatus.HEALTHY
    await _clear_worker_heartbeats(sessions)
    await engine.dispose()


def _settings(tmp_path: Path, *, deepseek_api_key: SecretStr | None) -> Settings:
    return Settings(
        database_url=os.environ["AWESOME_AGENT_TEST_DATABASE_URL"],
        checkpoint_database_url=os.environ[
            "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL"
        ],
        deepseek_api_key=deepseek_api_key,
        workspace_root=tmp_path / "workspaces",
        worker_heartbeat_stale_seconds=120,
    )


async def _clear_worker_heartbeats(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as session:
        await session.execute(delete(WorkerHeartbeatRecord))


def _check(report: ReadinessReport, name: str) -> HealthCheck:
    return next(check for check in report.checks if check.name == name)
