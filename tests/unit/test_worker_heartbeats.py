from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from awesome_agent.health import HealthStatus
from awesome_agent.runtime.worker_heartbeats import (
    GraphIdentity,
    InMemoryWorkerHeartbeatRepository,
    WorkerHeartbeat,
    WorkerHeartbeatStatus,
    worker_heartbeat_check,
)
from awesome_agent.settings import Settings


def test_graph_identity_label_is_name_only() -> None:
    assert GraphIdentity("solo-readonly").label() == "solo-readonly"


@pytest.mark.asyncio
async def test_in_memory_worker_heartbeat_reports_fresh_worker() -> None:
    repository = InMemoryWorkerHeartbeatRepository()
    worker_id = uuid4()
    now = datetime.now(UTC)

    await repository.upsert(
        WorkerHeartbeat(
            worker_id=worker_id,
            worker_name="worker-a",
            started_at=now,
            heartbeat_at=now,
            supported_graphs=[GraphIdentity("solo-readonly")],
            status=WorkerHeartbeatStatus.ONLINE,
        )
    )

    workers = await repository.list_recent(stale_after=now - timedelta(seconds=120))

    assert workers[0].worker_id == worker_id


@pytest.mark.asyncio
async def test_worker_heartbeat_check_requires_fresh_matching_graph() -> None:
    repository = InMemoryWorkerHeartbeatRepository()
    now = datetime.now(UTC)
    await repository.upsert(
        WorkerHeartbeat(
            worker_id=uuid4(),
            worker_name="worker-a",
            started_at=now,
            heartbeat_at=now,
            supported_graphs=[GraphIdentity("solo-readonly")],
            status=WorkerHeartbeatStatus.ONLINE,
        )
    )

    check = await worker_heartbeat_check(
        repository,
        Settings(worker_heartbeat_stale_seconds=120),
        required_graphs=[GraphIdentity("solo-readonly")],
        now=now,
    )

    assert check.status is HealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_worker_heartbeat_check_reports_unhealthy_without_fresh_worker() -> None:
    repository = InMemoryWorkerHeartbeatRepository()
    now = datetime.now(UTC)

    check = await worker_heartbeat_check(
        repository,
        Settings(worker_heartbeat_stale_seconds=120),
        required_graphs=[GraphIdentity("solo-readonly")],
        now=now,
    )

    assert check.status is HealthStatus.UNHEALTHY
    assert check.name == "worker_heartbeat"
