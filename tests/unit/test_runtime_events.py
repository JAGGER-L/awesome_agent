from pathlib import Path

import pytest

from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import RunStatus
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.service import RuntimeService


@pytest.mark.asyncio
async def test_runtime_service_emits_traceable_events(tmp_path: Path) -> None:
    events = EventStream()
    service = RuntimeService(
        events=events,
        artifacts=LocalArtifactStore(tmp_path),
    )

    run = await service.create_run("Implement feature")
    cancelled = await service.cancel_run(run.id)
    history = events.history(run.id)

    assert cancelled.status is RunStatus.CANCELLED
    assert [event.sequence for event in history] == [1, 2, 3]
    assert history[1].agent_id == service.list_agents(run.id)[0].id


@pytest.mark.asyncio
async def test_event_stream_replays_after_cursor(tmp_path: Path) -> None:
    events = EventStream()
    service = RuntimeService(
        events=events,
        artifacts=LocalArtifactStore(tmp_path),
    )
    run = await service.create_run("Goal")

    assert [event.sequence for event in events.history(run.id, after_sequence=1)] == [2]

    subscription = events.subscribe(run.id, after_sequence=1)
    replayed = await anext(subscription)
    await subscription.aclose()

    assert replayed.sequence == 2
