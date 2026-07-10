import pytest
from pydantic import ValidationError

from awesome_agent.core.events import (
    CollectingEventSink,
    EventEmitter,
    EventEnvelope,
    EventType,
    ToolStartedPayload,
)


@pytest.mark.asyncio
async def test_emitter_assigns_monotonic_sequence() -> None:
    sink = CollectingEventSink()
    emitter = EventEmitter(session_id="session_1", sink=sink)

    first = await emitter.emit(ToolStartedPayload(call_id="call_1", tool_name="ls"))
    second = await emitter.emit(
        ToolStartedPayload(call_id="call_2", tool_name="grep"),
        turn_id="turn_1",
    )

    assert [first.sequence, second.sequence] == [1, 2]
    assert sink.events == [first, second]
    assert EventEnvelope.model_validate_json(first.model_dump_json()) == first


def test_event_type_must_match_payload_kind() -> None:
    with pytest.raises(ValidationError):
        EventEnvelope(
            session_id="session_1",
            turn_id=None,
            sequence=1,
            event_type=EventType.OPERATION_COMPLETED,
            payload=ToolStartedPayload(call_id="call_1", tool_name="ls"),
        )
