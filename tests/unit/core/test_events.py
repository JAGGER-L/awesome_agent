from datetime import UTC
from typing import Any, cast

import pytest
from pydantic import ValidationError

from awesome_agent.core.events import (
    _MAX_LIFECYCLE_HISTORY,
    AssistantTextDeltaPayload,
    CollectingEventSink,
    EventEmitter,
    EventEnvelope,
    EventLifecycleError,
    EventType,
    InteractionChoicePayload,
    InteractionResolvedPayload,
    OperationLifecyclePayload,
    ToolResultPayload,
    ToolStartedPayload,
    TurnLifecyclePayload,
)


class DiscardingEventSink:
    async def emit(self, event: EventEnvelope) -> None:
        del event


@pytest.mark.asyncio
async def test_emitter_assigns_complete_identity_and_monotonic_sequence() -> None:
    sink = CollectingEventSink()
    emitter = EventEmitter(
        session_id="session_1",
        workspace_key="workspace_1",
        sink=sink,
    )

    first = await emitter.emit(
        ToolStartedPayload(
            call_id="call_1",
            tool_name="ls",
            verb="List",
            target=".",
        ),
        thread_id="thread_1",
        turn_id="turn_1",
        operation_id="operation_1",
    )
    second = await emitter.emit(AssistantTextDeltaPayload(text="done"))

    assert [first.sequence, second.sequence] == [1, 2]
    assert first.event_id.startswith("event_")
    assert first.session_id == "session_1"
    assert first.workspace_key == "workspace_1"
    assert first.thread_id == "thread_1"
    assert first.turn_id == "turn_1"
    assert first.operation_id == "operation_1"
    assert first.timestamp.tzinfo is UTC
    assert sink.events == [first, second]
    assert EventEnvelope.model_validate_json(first.model_dump_json()) == first


def test_event_type_must_match_payload_kind() -> None:
    with pytest.raises(ValidationError):
        EventEnvelope(
            event_id="event_1",
            session_id="session_1",
            workspace_key="workspace_1",
            sequence=1,
            event_type=EventType.OPERATION_COMPLETED,
            payload=ToolStartedPayload(
                call_id="call_1",
                tool_name="ls",
                verb="List",
            ),
        )


def test_interaction_event_decisions_are_closed() -> None:
    invalid = cast(Any, "future")
    with pytest.raises(ValidationError):
        InteractionChoicePayload(decision=invalid, label="Future")
    with pytest.raises(ValidationError):
        InteractionResolvedPayload(
            interaction_id="interaction_1",
            decision=invalid,
        )


@pytest.mark.asyncio
async def test_lifecycle_events_require_identity_and_one_terminal() -> None:
    sink = CollectingEventSink()
    emitter = EventEmitter(
        session_id="session_1",
        workspace_key="workspace_1",
        sink=sink,
    )

    with pytest.raises(EventLifecycleError, match="operation_id"):
        await emitter.emit(OperationLifecyclePayload(kind=EventType.OPERATION_STARTED))

    await emitter.emit(
        OperationLifecyclePayload(kind=EventType.OPERATION_STARTED),
        operation_id="operation_1",
    )
    await emitter.emit(
        OperationLifecyclePayload(kind=EventType.OPERATION_COMPLETED),
        operation_id="operation_1",
    )
    with pytest.raises(EventLifecycleError, match="terminal"):
        await emitter.emit(
            OperationLifecyclePayload(kind=EventType.OPERATION_FAILED),
            operation_id="operation_1",
        )

    await emitter.emit(
        TurnLifecyclePayload(kind=EventType.TURN_STARTED),
        thread_id="thread_1",
        turn_id="turn_1",
    )
    await emitter.emit(
        TurnLifecyclePayload(kind=EventType.TURN_CANCELLED, duration_ms=1),
        thread_id="thread_1",
        turn_id="turn_1",
    )
    with pytest.raises(EventLifecycleError, match="terminal"):
        await emitter.emit(
            TurnLifecyclePayload(kind=EventType.TURN_COMPLETED, duration_ms=2),
            thread_id="thread_1",
            turn_id="turn_1",
        )


@pytest.mark.asyncio
async def test_completed_lifecycle_identity_history_is_bounded() -> None:
    emitter = EventEmitter(
        session_id="session_1",
        workspace_key="workspace_1",
        sink=DiscardingEventSink(),
    )

    for index in range(_MAX_LIFECYCLE_HISTORY + 10):
        operation_id = f"operation_{index}"
        await emitter.emit(
            OperationLifecyclePayload(kind=EventType.OPERATION_STARTED),
            operation_id=operation_id,
        )
        await emitter.emit(
            OperationLifecyclePayload(kind=EventType.OPERATION_COMPLETED),
            operation_id=operation_id,
        )

    assert emitter._started_operations == set()
    assert len(emitter._terminal_operations) == _MAX_LIFECYCLE_HISTORY
    assert "operation_0" not in emitter._terminal_operations


def test_payloads_reject_unbounded_or_unknown_metadata() -> None:
    with pytest.raises(ValidationError):
        AssistantTextDeltaPayload(text="x" * 30_001)
    with pytest.raises(ValidationError):
        ToolStartedPayload(
            call_id="call_1",
            tool_name="ls",
            traceback="secret",  # type: ignore[call-arg]
        )


def test_tool_terminal_presentation_requires_measured_facts() -> None:
    payload = ToolResultPayload(
        kind=EventType.TOOL_COMPLETED,
        call_id="call_1",
        tool_name="write_file",
        verb="Write",
        target="circle_area.py",
        outcome="Created",
        summary="21 lines",
        detail=None,
        duration_ms=18,
    )

    assert payload.duration_ms == 18
    assert payload.outcome == "Created"
