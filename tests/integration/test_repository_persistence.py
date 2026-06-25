from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete

from awesome_agent.domain.enums import (
    AgentKind,
    DispatchStatus,
    EventType,
    RunIntent,
    WorkspaceState,
)
from awesome_agent.domain.models import (
    Agent,
    IntakeReservation,
    Repository,
    Run,
    RuntimeEvent,
)
from awesome_agent.persistence.database import (
    create_engine,
    create_session_factory,
)
from awesome_agent.persistence.intake_reservations import (
    PostgresIntakeReservationStore,
)
from awesome_agent.persistence.models import (
    IntakeReservationRecord,
    RepositoryRecord,
    RunRecord,
)
from awesome_agent.persistence.repository_registry import (
    PostgresRepositoryRegistry,
)
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_repository_reservation_and_run_round_trip(tmp_path: Path) -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    registry = PostgresRepositoryRegistry(sessions)
    reservations = PostgresIntakeReservationStore(sessions)
    runtime = PostgresRuntimeRepository(sessions)

    repository = await registry.upsert(
        Repository(
            root=tmp_path / "repository",
            display_name="repository",
            git_common_dir=tmp_path / "repository" / ".git",
            default_branch="main",
        )
    )
    run_id = uuid4()
    workspace = tmp_path / "worktree"
    branch = f"awesome-agent/run/{run_id}"
    reservation = IntakeReservation(
        run_id=run_id,
        repository_id=repository.id,
        base_commit="a" * 40,
        intent=RunIntent.MODIFYING,
        workspace_path=workspace,
        integration_branch=branch,
    )
    await reservations.create(reservation)
    run = Run(
        id=run_id,
        goal="Persist intake",
        repository_id=repository.id,
        base_commit=reservation.base_commit,
        intent=reservation.intent,
        dispatch_status=DispatchStatus.QUEUED,
        workspace_path=workspace,
        integration_branch=branch,
        workspace_state=WorkspaceState.READY,
        graph_thread_id=f"run:{run_id}",
    )
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="deepseek-v4-pro",
    )
    events = [
        RuntimeEvent(
            run_id=run.id,
            sequence=1,
            event_type=EventType.RUN_CREATED,
            payload={"goal": run.goal},
        ),
        RuntimeEvent(
            run_id=run.id,
            sequence=2,
            event_type=EventType.AGENT_CREATED,
            payload={"agent_id": str(leader.id)},
            agent_id=leader.id,
        ),
    ]
    await runtime.publish_intake(
        run=run,
        leader=leader,
        events=events,
        reservation_id=reservation.id,
    )

    assert await registry.get(repository.id) == repository
    assert await runtime.get_run(run.id) == run
    assert await runtime.list_events(run.id) == events
    assert await reservations.list_incomplete() == []

    async with sessions.begin() as session:
        await session.execute(delete(RunRecord).where(RunRecord.id == run.id))
        await session.execute(
            delete(IntakeReservationRecord).where(
                IntakeReservationRecord.id == reservation.id
            )
        )
        await session.execute(
            delete(RepositoryRecord).where(RepositoryRecord.id == repository.id)
        )
    await engine.dispose()
