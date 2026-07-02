from __future__ import annotations

from uuid import uuid4

import pytest

from awesome_agent.conversation.events import (
    ConversationStreamEvent,
    ConversationStreamEventKind,
    UnknownConversationStreamEvent,
    parse_conversation_stream_event,
)


def test_conversation_stream_event_serializes_every_kind() -> None:
    thread_id = uuid4()
    turn_id = uuid4()

    for sequence, kind in enumerate(ConversationStreamEventKind, start=1):
        event = ConversationStreamEvent(
            event=kind,
            thread_id=thread_id,
            turn_id=turn_id,
            sequence=sequence,
            trace_id="trace-1",
            payload={"kind": kind.value},
        )

        payload = event.model_dump(mode="json")
        parsed = parse_conversation_stream_event(payload)

        assert payload["event"] == kind.value
        assert parsed == event


def test_unknown_conversation_stream_event_is_structured_error() -> None:
    payload = {
        "event": "future.event",
        "thread_id": str(uuid4()),
        "turn_id": str(uuid4()),
        "sequence": 1,
        "trace_id": "trace-1",
        "payload": {},
    }

    with pytest.raises(UnknownConversationStreamEvent):
        parse_conversation_stream_event(payload)
