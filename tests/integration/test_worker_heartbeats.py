from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete

from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.models import WorkerHeartbeatRecord
from awesome_agent.persistence.worker_heartbeats import (
    PostgresWorkerHeartbeatRepository,
)
from awesome_agent.runtime.worker_heartbeats import (
    GraphIdentity,
    WorkerHeartbeat,
    WorkerHeartbeatStatus,
)

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_postgres_worker_heartbeat_repository_round_trip() -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    repository = PostgresWorkerHeartbeatRepository(sessions)
    worker_id = uuid4()
    now = datetime.now(UTC)

    await repository.upsert(
        WorkerHeartbeat(
            worker_id=worker_id,
            worker_name="worker-a",
            started_at=now,
            heartbeat_at=now,
            supported_graphs=[
                GraphIdentity("runtime-probe", 1),
                GraphIdentity("solo-readonly", 1),
            ],
            status=WorkerHeartbeatStatus.ONLINE,
        )
    )

    fresh = await repository.list_recent(stale_after=now - timedelta(seconds=120))

    assert [worker.worker_id for worker in fresh] == [worker_id]
    assert fresh[0].supported_graphs == [
        GraphIdentity("runtime-probe", 1),
        GraphIdentity("solo-readonly", 1),
    ]

    await repository.mark_stopping(worker_id)
    stopping = await repository.list_recent(stale_after=now - timedelta(seconds=120))

    assert stopping[0].status is WorkerHeartbeatStatus.STOPPING

    async with sessions.begin() as session:
        await session.execute(
            delete(WorkerHeartbeatRecord).where(
                WorkerHeartbeatRecord.worker_id == worker_id
            )
        )
    await engine.dispose()
