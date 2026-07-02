from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

from awesome_agent.conversation.events import (
    ConversationStreamEventKind,
    parse_conversation_stream_event,
)
from awesome_agent.conversation.service import ConversationService
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.stream import (
    ModelStreamEvent,
    ReasoningDelta,
    ReasoningStarted,
    TextDelta,
    TurnCompleted,
)
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, StopReason
from awesome_agent.persistence.conversations import InMemoryConversationRepository


def test_reasoning_events_parse_from_sse_payloads() -> None:
    payload = {
        "event": "reasoning.delta",
        "thread_id": str(uuid4()),
        "turn_id": str(uuid4()),
        "sequence": 1,
        "trace_id": "trace-1",
        "payload": {"text": "Inspecting context.", "extra": True},
    }

    parsed = parse_conversation_stream_event(payload)

    assert parsed.event is ConversationStreamEventKind.REASONING_DELTA
    assert parsed.payload["text"] == "Inspecting context."


async def test_conversation_service_emits_reasoning_events_before_answer() -> None:
    repository = InMemoryConversationRepository()
    thread = await repository.create_thread(title="Reasoning")
    service = ConversationService(
        repository=repository,
        provider_factory=lambda _model: ReasoningProvider(),
        default_model="fake-model",
    )

    events = [
        event
        async for event in service.start_turn(thread_id=thread.id, content="hi")
    ]

    assert [event.event for event in events] == [
        ConversationStreamEventKind.TURN_STARTED,
        ConversationStreamEventKind.MESSAGE_CREATED,
        ConversationStreamEventKind.REASONING_STARTED,
        ConversationStreamEventKind.REASONING_DELTA,
        ConversationStreamEventKind.MESSAGE_DELTA,
        ConversationStreamEventKind.REASONING_COMPLETED,
        ConversationStreamEventKind.MESSAGE_COMPLETED,
        ConversationStreamEventKind.TURN_COMPLETED,
    ]
    assert events[3].payload == {"text": "Inspect context."}
    assert events[5].payload == {"failed": False}


class ReasoningProvider:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        yield ReasoningStarted()
        yield ReasoningDelta(text="Inspect context.")
        yield TextDelta(text="hello")
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content="hello"),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            )
        )
