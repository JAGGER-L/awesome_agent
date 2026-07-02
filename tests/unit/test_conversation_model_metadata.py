from __future__ import annotations

from collections.abc import AsyncIterator

from awesome_agent.conversation.events import ConversationStreamEventKind
from awesome_agent.conversation.service import ConversationService
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.stream import ModelStreamEvent, TextDelta, TurnCompleted
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, StopReason
from awesome_agent.persistence.conversations import InMemoryConversationRepository


async def test_conversation_completion_includes_model_metadata() -> None:
    repository = InMemoryConversationRepository()
    thread = await repository.create_thread(title="Model metadata")
    service = ConversationService(
        repository=repository,
        provider_factory=lambda _model: MetadataProvider(),
        default_model="deepseek-v4-pro",
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


class MetadataProvider:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        yield TextDelta(text="hello")
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content="hello"),
                stop_reason=StopReason.COMPLETED,
                model="deepseek-v4-pro",
                provider="deepseek",
                response_id="response-123",
            )
        )
