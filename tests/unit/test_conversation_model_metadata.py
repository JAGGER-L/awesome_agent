from __future__ import annotations

from tests.conversation_projection_fakes import ProjectedConversationRunIntake

from awesome_agent.conversation.events import ConversationStreamEventKind
from awesome_agent.conversation.service import ConversationService
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.runtime.repository import InMemoryRuntimeRepository


async def test_conversation_completion_includes_model_metadata() -> None:
    repository = InMemoryConversationRepository()
    thread = await repository.create_thread(
        title="Model metadata",
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
            response_metadata={
                "requested_model": "deepseek-v4-pro",
                "response_model": "deepseek-v4-pro",
                "provider": "deepseek",
                "response_id": "response-123",
            },
        ),
        default_model="deepseek-v4-pro",
        event_poll_interval=0,
    )

    events = [
        event async for event in service.start_turn(thread_id=thread.id, content="hi")
    ]
    completed = next(
        event
        for event in events
        if event.event is ConversationStreamEventKind.MESSAGE_COMPLETED
    )

    assert completed.payload["requested_model"] == "deepseek-v4-pro"
    assert completed.payload["response_model"] == "deepseek-v4-pro"
    assert completed.payload["provider"] == "deepseek"
    assert completed.payload["response_id"] == "response-123"
