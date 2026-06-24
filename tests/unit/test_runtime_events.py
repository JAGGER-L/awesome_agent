from pathlib import Path

import pytest

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import RunStatus
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.service import RuntimeService


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="deepseek-v4-pro",
        teammate_model="deepseek-v4-flash",
        verifier_model="deepseek-v4-flash",
        subagent_model="deepseek-v4-flash",
    )


@pytest.mark.asyncio
async def test_runtime_service_emits_traceable_events(tmp_path: Path) -> None:
    events = EventStream()
    service = RuntimeService(
        repository=InMemoryRuntimeRepository(),
        events=events,
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
    )

    run = await service.create_run("Implement feature")
    cancelled = await service.cancel_run(run.id)
    history = await service.list_events(run.id)

    assert cancelled.status is RunStatus.CANCELLED
    assert [event.sequence for event in history] == [1, 2, 3]
    assert history[1].agent_id == (await service.list_agents(run.id))[0].id


@pytest.mark.asyncio
async def test_event_stream_replays_after_cursor(tmp_path: Path) -> None:
    events = EventStream()
    service = RuntimeService(
        repository=InMemoryRuntimeRepository(),
        events=events,
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
    )
    run = await service.create_run("Goal")

    history = await service.list_events(run.id, after_sequence=1)
    assert [event.sequence for event in history] == [2]

    subscription = service.stream_events(run.id, after_sequence=1)
    replayed = await anext(subscription)
    await subscription.aclose()

    assert replayed.sequence == 2
