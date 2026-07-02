from __future__ import annotations

from collections.abc import AsyncIterator

from awesome_agent.conversation.events import ConversationStreamEventKind
from awesome_agent.conversation.models import ThreadMessageRole
from awesome_agent.conversation.service import ConversationService
from awesome_agent.modeling.errors import ModelErrorCode, ModelErrorInfo
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.stream import (
    ModelStreamEvent,
    TextDelta,
    TurnCompleted,
    TurnFailed,
)
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, ModelUsage, StopReason
from awesome_agent.persistence.conversations import InMemoryConversationRepository


async def test_conversation_service_streams_and_persists_assistant_message() -> None:
    repository = InMemoryConversationRepository()
    thread = await repository.create_thread(title="Greeting")
    service = ConversationService(
        repository=repository,
        provider_factory=lambda _model: FakeStreamingProvider(),
        default_model="fake-model",
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


async def test_conversation_service_emits_error_without_assistant_message() -> None:
    repository = InMemoryConversationRepository()
    thread = await repository.create_thread(title="Failure")
    service = ConversationService(
        repository=repository,
        provider_factory=lambda _model: FailingStreamingProvider(),
        default_model="fake-model",
    )

    events = [
        event
        async for event in service.start_turn(thread_id=thread.id, content="hello?")
    ]
    messages = await repository.list_messages(thread.id)

    assert events[-1].event is ConversationStreamEventKind.ERROR
    assert events[-1].payload["code"] == "invalid_request"
    assert [message.role for message in messages] == [ThreadMessageRole.USER]


class FakeStreamingProvider:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        assert request.messages[-1].role == "user"
        yield TextDelta(text="hello")
        yield TextDelta(text=" world")
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content="hello world"),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
                usage=ModelUsage(input_tokens=1, output_tokens=2),
            )
        )


class FailingStreamingProvider:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        yield TurnFailed(
            error=ModelErrorInfo(
                code=ModelErrorCode.INVALID_REQUEST,
                message="bad request",
                retryable=False,
                provider="fake",
                status_code=400,
            )
        )
