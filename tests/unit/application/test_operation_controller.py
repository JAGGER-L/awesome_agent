import asyncio

import pytest

from awesome_agent.application.operations import OperationBusy, OperationController
from awesome_agent.core.events import CollectingEventSink, EventEmitter, EventType


@pytest.mark.asyncio
async def test_operation_serialization_and_completed_terminal_event() -> None:
    sink = CollectingEventSink()
    controller = OperationController(EventEmitter(session_id="session_1", sink=sink))
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked(operation_id: str) -> str:
        started.set()
        await release.wait()
        return operation_id

    first = asyncio.create_task(controller.run(blocked, turn_id="turn_1"))
    await started.wait()
    active_id = controller.active_operation_id
    assert active_id is not None

    with pytest.raises(OperationBusy):
        await controller.run(blocked)

    release.set()
    assert await first == active_id
    assert [event.event_type for event in sink.events] == [
        EventType.OPERATION_COMPLETED
    ]
    assert sink.events[0].turn_id == "turn_1"
    assert controller.active_operation_id is None


@pytest.mark.asyncio
async def test_operation_failure_emits_one_failed_terminal_event() -> None:
    sink = CollectingEventSink()
    controller = OperationController(EventEmitter(session_id="session_1", sink=sink))

    async def fail(operation_id: str) -> None:
        raise RuntimeError(operation_id)

    with pytest.raises(RuntimeError):
        await controller.run(fail)

    assert [event.event_type for event in sink.events] == [EventType.OPERATION_FAILED]


@pytest.mark.asyncio
async def test_cancel_by_id_emits_one_cancelled_terminal_event() -> None:
    sink = CollectingEventSink()
    controller = OperationController(EventEmitter(session_id="session_1", sink=sink))
    started = asyncio.Event()

    async def blocked(operation_id: str) -> None:
        started.set()
        await asyncio.Event().wait()

    running = asyncio.create_task(controller.run(blocked))
    await started.wait()
    operation_id = controller.active_operation_id
    assert operation_id is not None

    assert await controller.cancel("operation_other") is False
    assert await controller.cancel(operation_id) is True
    with pytest.raises(asyncio.CancelledError):
        await running

    assert [event.event_type for event in sink.events] == [
        EventType.OPERATION_CANCELLED
    ]
    assert controller.active_operation_id is None
