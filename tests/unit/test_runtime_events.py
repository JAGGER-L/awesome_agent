from pathlib import Path
from uuid import UUID

import pytest

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import DispatchStatus, EventType, RunStatus
from awesome_agent.domain.models import RuntimeEvent
from awesome_agent.runtime.dispatch import DispatchConflict
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


class FakeCancellationDispatcher:
    def __init__(self) -> None:
        self.cancelled: list[UUID] = []

    async def request_cancellation(
        self,
        *,
        run_id: UUID,
        requested_by: str | None,
        reason: str | None,
    ) -> RuntimeEvent:
        self.cancelled.append(run_id)
        return RuntimeEvent(
            run_id=run_id,
            sequence=99,
            event_type=EventType.CANCELLATION_REQUESTED,
            payload={"requested_by": requested_by, "reason": reason},
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
    repeated = await service.cancel_run(run.id)
    history = await service.list_events(run.id)

    assert cancelled.status is RunStatus.CANCELLED
    assert repeated.status is RunStatus.CANCELLED
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


@pytest.mark.asyncio
async def test_claimed_run_cancellation_is_rejected(tmp_path: Path) -> None:
    repository = InMemoryRuntimeRepository()
    service = RuntimeService(
        repository=repository,
        events=EventStream(),
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
    )
    run = await service.create_run("Goal")
    await repository.update_run(
        run.model_copy(update={"dispatch_status": DispatchStatus.CLAIMED})
    )

    with pytest.raises(DispatchConflict):
        await service.cancel_run(run.id)


@pytest.mark.asyncio
async def test_dispatcher_backed_cancellation_accepts_claimed_run(
    tmp_path: Path,
) -> None:
    repository = InMemoryRuntimeRepository()
    dispatcher = FakeCancellationDispatcher()
    service = RuntimeService(
        repository=repository,
        events=EventStream(),
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
        dispatcher=dispatcher,  # type: ignore[arg-type]
    )
    run = await service.create_run("Goal")
    await repository.update_run(
        run.model_copy(update={"dispatch_status": DispatchStatus.CLAIMED})
    )

    current = await service.cancel_run(run.id)

    assert current.dispatch_status is DispatchStatus.CLAIMED
    assert dispatcher.cancelled == [run.id]
