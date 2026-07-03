from __future__ import annotations

from uuid import uuid4

from tests.conversation_projection_fakes import ProjectedConversationRunIntake

from awesome_agent.conversation.events import (
    ConversationStreamEventKind,
    parse_conversation_stream_event,
)
from awesome_agent.conversation.service import ConversationService
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.runtime.repository import InMemoryRuntimeRepository


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
    thread = await repository.create_thread(
        title="Reasoning",
        context_path="E:/project",
    )
    runtime = InMemoryRuntimeRepository()
    service = ConversationService(
        repository=repository,
        runtime_repository=runtime,
        conversation_run_intake=ProjectedConversationRunIntake(
            conversations=repository,
            runtime=runtime,
            assistant_content="hello",
            text_deltas=("hello",),
            reasoning=True,
        ),
        default_model="fake-model",
        event_poll_interval=0,
    )

    events = [
        event async for event in service.start_turn(thread_id=thread.id, content="hi")
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
