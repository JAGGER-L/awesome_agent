import asyncio

import pytest

from awesome_agent.application.events import ApplicationEventProjector
from awesome_agent.application.foreground import ForegroundArbiter, ForegroundBusy
from awesome_agent.application.operations import OperationBusy, OperationController
from awesome_agent.core.events import (
    CollectingEventSink,
    EventEmitter,
    EventEnvelope,
    EventType,
)
from awesome_agent.core.tools import (
    ToolError,
    ToolErrorCode,
    ToolPresentation,
    ToolResult,
    ToolStatus,
)
from awesome_agent.modeling import (
    ModelErrorCode,
    ModelErrorInfo,
    ProviderRetrying,
    ReasoningDelta,
    TextDelta,
    TurnFailed,
)


def _emitter(sink: CollectingEventSink) -> EventEmitter:
    return EventEmitter(
        session_id="session_1",
        workspace_key="workspace_1",
        sink=sink,
    )


class BlockingOperationSink:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def emit(self, event: EventEnvelope) -> None:
        assert event.event_type is EventType.OPERATION_STARTED
        self.entered.set()
        await self.release.wait()


class CancelAfterStartedEventSink:
    async def emit(self, event: EventEnvelope) -> None:
        if event.event_type is EventType.OPERATION_STARTED:
            owner = asyncio.current_task()
            assert owner is not None
            asyncio.get_running_loop().call_soon(owner.cancel)


@pytest.mark.asyncio
async def test_operation_serialization_and_completed_terminal_event() -> None:
    sink = CollectingEventSink()
    controller = OperationController(_emitter(sink))
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
        EventType.OPERATION_STARTED,
        EventType.OPERATION_COMPLETED,
    ]
    assert {event.operation_id for event in sink.events} == {active_id}
    assert {event.turn_id for event in sink.events} == {"turn_1"}
    assert controller.active_operation_id is None


@pytest.mark.asyncio
async def test_operation_and_exclusive_leases_are_atomic_in_both_directions() -> None:
    foreground = ForegroundArbiter()
    controller = OperationController(
        _emitter(CollectingEventSink()),
        foreground,
    )
    exclusive = foreground.acquire_exclusive()

    with pytest.raises(OperationBusy):
        controller.reserve()

    exclusive.release()
    reservation = controller.reserve()
    with pytest.raises(ForegroundBusy):
        foreground.acquire_exclusive()
    controller.abort(reservation)


@pytest.mark.asyncio
async def test_aborted_reservation_releases_foreground_without_events() -> None:
    foreground = ForegroundArbiter()
    controller = OperationController(
        _emitter(CollectingEventSink()),
        foreground,
    )
    reservation = controller.reserve()

    controller.abort(reservation)

    assert controller.active_operation_id is None
    lease = foreground.acquire_exclusive()
    lease.release()


@pytest.mark.asyncio
async def test_shutdown_cancels_unpublished_operation_task() -> None:
    foreground = ForegroundArbiter()
    sink = BlockingOperationSink()
    controller = OperationController(
        EventEmitter(
            session_id="session_1",
            workspace_key="workspace_1",
            sink=sink,
        ),
        foreground,
    )
    factory_called = False

    async def factory(operation_id: str) -> None:
        nonlocal factory_called
        del operation_id
        factory_called = True

    starter = asyncio.create_task(controller.start(factory))
    await sink.entered.wait()
    foreground.begin_closing()

    await asyncio.wait_for(controller.shutdown(), timeout=1)

    with pytest.raises(asyncio.CancelledError):
        await starter
    await asyncio.wait_for(foreground.wait_idle(), timeout=1)
    assert controller.active_operation_id is None
    assert foreground.active_kind is None
    assert factory_called is False


@pytest.mark.asyncio
async def test_cancel_during_child_publication_cancels_and_waits_for_child() -> None:
    controller = OperationController(
        EventEmitter(
            session_id="session_1",
            workspace_key="workspace_1",
            sink=CancelAfterStartedEventSink(),
        )
    )
    factory_started = asyncio.Event()
    factory_cancelled = False

    async def factory(operation_id: str) -> None:
        nonlocal factory_cancelled
        del operation_id
        factory_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            factory_cancelled = True
            raise

    starter = asyncio.create_task(controller.start(factory))
    with pytest.raises(asyncio.CancelledError):
        await starter
    released_before_cleanup = controller.active_operation_id is None
    await controller.shutdown()

    assert released_before_cleanup is True
    assert not factory_started.is_set() or factory_cancelled is True


@pytest.mark.asyncio
async def test_operation_failure_emits_one_failed_terminal_event() -> None:
    sink = CollectingEventSink()
    controller = OperationController(_emitter(sink))

    async def fail(operation_id: str) -> None:
        raise RuntimeError(operation_id)

    with pytest.raises(RuntimeError):
        await controller.run(fail)

    assert [event.event_type for event in sink.events] == [
        EventType.OPERATION_STARTED,
        EventType.OPERATION_FAILED,
    ]


@pytest.mark.asyncio
async def test_cancel_by_id_emits_one_cancelled_terminal_event() -> None:
    sink = CollectingEventSink()
    controller = OperationController(_emitter(sink))
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
        EventType.OPERATION_STARTED,
        EventType.OPERATION_CANCELLED,
    ]
    assert controller.active_operation_id is None


@pytest.mark.asyncio
async def test_projector_normalizes_gateway_tool_and_turn_events() -> None:
    sink = CollectingEventSink()
    projector = ApplicationEventProjector(
        emitter=_emitter(sink),
        thread_id="thread_1",
        turn_id="turn_1",
        operation_id="operation_1",
        client_message_id="client_1",
    )

    await projector.turn_started()
    await projector.project_gateway(TextDelta(text="answer"))
    await projector.project_gateway(ReasoningDelta(text="private reasoning"))
    await projector.project_gateway(
        ProviderRetrying(
            attempt=2,
            maximum=3,
            delay_seconds=0.5,
            error_code=ModelErrorCode.TRANSIENT,
        )
    )
    await projector.project_tool(
        ToolResult(
            call_id="call_1",
            tool_name="read_file",
            status=ToolStatus.ERROR,
            content="raw file body must not be projected",
            error=ToolError(
                code=ToolErrorCode.NOT_FOUND,
                message="File was not found.",
            ),
            presentation=ToolPresentation(
                verb="Read",
                target="missing.py",
                outcome="Failed",
                summary="not_found",
                detail="File was not found.",
                duration_ms=7,
            ),
        )
    )
    await projector.project_gateway(
        TurnFailed(
            error=ModelErrorInfo(
                code=ModelErrorCode.INVALID_REQUEST,
                message="Safe provider-independent error.",
                retryable=False,
                provider="deepseek",
            )
        )
    )
    await projector.turn_failed("model_failed")

    assert [event.event_type for event in sink.events] == [
        EventType.TURN_STARTED,
        EventType.ASSISTANT_TEXT_DELTA,
        EventType.ASSISTANT_REASONING_DELTA,
        EventType.PROVIDER_RETRYING,
        EventType.TOOL_FAILED,
        EventType.WARNING,
        EventType.TURN_FAILED,
    ]
    tool_payload = sink.events[4].payload
    assert "raw file body" not in tool_payload.model_dump_json()
    assert tool_payload.duration_ms == 7  # type: ignore[union-attr]
    assert sink.events[-1].payload.duration_ms is not None  # type: ignore[union-attr]
    assert all(event.thread_id == "thread_1" for event in sink.events)
    assert all(event.turn_id == "turn_1" for event in sink.events)
    assert all(event.operation_id == "operation_1" for event in sink.events)


@pytest.mark.asyncio
async def test_projector_rejects_second_turn_terminal() -> None:
    projector = ApplicationEventProjector(
        emitter=_emitter(CollectingEventSink()),
        thread_id="thread_1",
        turn_id="turn_1",
        operation_id="operation_1",
        client_message_id="client_1",
    )
    await projector.turn_started()
    await projector.turn_completed()

    with pytest.raises(RuntimeError, match="terminal"):
        await projector.turn_cancelled("too_late")
