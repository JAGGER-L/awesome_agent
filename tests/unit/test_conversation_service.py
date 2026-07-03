from __future__ import annotations

from tests.conversation_projection_fakes import ProjectedConversationRunIntake

from awesome_agent.conversation.events import ConversationStreamEventKind
from awesome_agent.conversation.models import ThreadMessageRole
from awesome_agent.conversation.service import ConversationService
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.runtime.repository import InMemoryRuntimeRepository


async def test_conversation_service_projects_and_persists_assistant_message() -> None:
    repository = InMemoryConversationRepository()
    thread = await repository.create_thread(
        title="Greeting",
        context_path="E:/project",
    )
    runtime = InMemoryRuntimeRepository()
    intake = ProjectedConversationRunIntake(
        conversations=repository,
        runtime=runtime,
        usage={"input_tokens": 1, "output_tokens": 2},
    )
    service = ConversationService(
        repository=repository,
        runtime_repository=runtime,
        conversation_run_intake=intake,
        default_model="fake-model",
        event_poll_interval=0,
    )

    events = [
        event
        async for event in service.start_turn(thread_id=thread.id, content="hello?")
    ]
    messages = await repository.list_messages(thread.id)

    assert [event.event for event in events] == [
        ConversationStreamEventKind.TURN_STARTED,
        ConversationStreamEventKind.MESSAGE_CREATED,
        ConversationStreamEventKind.MESSAGE_DELTA,
        ConversationStreamEventKind.MESSAGE_DELTA,
        ConversationStreamEventKind.USAGE_UPDATED,
        ConversationStreamEventKind.MESSAGE_COMPLETED,
        ConversationStreamEventKind.TURN_COMPLETED,
    ]
    assert [message.role for message in messages] == [
        ThreadMessageRole.USER,
        ThreadMessageRole.ASSISTANT,
    ]
    assert messages[0].content == "hello?"
    assert messages[1].content == "hello world"
    assert events[4].payload["output_tokens"] == 2
    assert all(event.trace_id == events[0].trace_id for event in events)


async def test_conversation_service_projects_error_without_assistant_message() -> None:
    repository = InMemoryConversationRepository()
    thread = await repository.create_thread(
        title="Failure",
        context_path="E:/project",
    )
    runtime = InMemoryRuntimeRepository()
    intake = ProjectedConversationRunIntake(
        conversations=repository,
        runtime=runtime,
        assistant_content=None,
        text_deltas=(),
        fail=True,
    )
    service = ConversationService(
        repository=repository,
        runtime_repository=runtime,
        conversation_run_intake=intake,
        default_model="fake-model",
        event_poll_interval=0,
    )

    events = [
        event
        async for event in service.start_turn(thread_id=thread.id, content="hello?")
    ]
    messages = await repository.list_messages(thread.id)

    assert events[-1].event is ConversationStreamEventKind.ERROR
    assert events[-1].payload["message"] == "bad request"
    assert [message.role for message in messages] == [ThreadMessageRole.USER]
