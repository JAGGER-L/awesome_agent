import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.api.app import _format_sse
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import EventType
from awesome_agent.domain.models import RuntimeEvent
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.service import RuntimeService


def test_sse_contains_cursor_type_and_json() -> None:
    event = RuntimeEvent(
        run_id=uuid4(),
        sequence=7,
        event_type=EventType.RUN_CREATED,
        payload={"goal": "test"},
    )

    output = _format_sse(event)

    assert output.startswith("id: 7\nevent: run.created\n")
    assert '"sequence":7' in output


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="deepseek-v4-pro",
        teammate_model="deepseek-v4-flash",
        verifier_model="deepseek-v4-flash",
        subagent_model="deepseek-v4-flash",
    )


@pytest.mark.asyncio
async def test_event_stream_observes_another_service(
    tmp_path: Path,
) -> None:
    repository = InMemoryRuntimeRepository()
    first = RuntimeService(
        repository=repository,
        events=EventStream(),
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
        event_poll_interval=0.001,
    )
    second = RuntimeService(
        repository=repository,
        events=EventStream(),
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
        event_poll_interval=0.001,
    )
    run = await first.create_run("Cross-process event")
    stream = second.stream_events(run.id, after_sequence=2)
    pending = asyncio.create_task(anext(stream))
    await first.decide_approval(
        run.id,
        approval_id=uuid4(),
        approved=True,
    )

    event = await asyncio.wait_for(pending, timeout=1)
    await stream.aclose()

    assert event.event_type is EventType.APPROVAL_DECIDED
