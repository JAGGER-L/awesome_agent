from __future__ import annotations

import os
from pathlib import Path

import pytest

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.persistence.database import (
    create_engine,
    create_session_factory,
)
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.service import RuntimeService

pytestmark = pytest.mark.integration


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="deepseek-v4-pro",
        teammate_model="deepseek-v4-flash",
        verifier_model="deepseek-v4-flash",
        subagent_model="deepseek-v4-flash",
    )


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_runtime_read_model_survives_service_restart(tmp_path: Path) -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)

    first = RuntimeService(
        repository=PostgresRuntimeRepository(sessions),
        events=EventStream(),
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
    )
    run = await first.create_run("Persist across restart")
    await first.cancel_run(run.id)

    second = RuntimeService(
        repository=PostgresRuntimeRepository(sessions),
        events=EventStream(),
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
    )

    restored = await second.get_run(run.id)
    agents = await second.list_agents(run.id)
    events = await second.list_events(run.id)

    assert restored.status.value == "cancelled"
    assert len(agents) == 1
    assert agents[0].model == "deepseek-v4-pro"
    assert [event.sequence for event in events] == [1, 2, 3]
    await engine.dispose()
