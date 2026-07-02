from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi.testclient import TestClient

from awesome_agent.api.app import create_app
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
from awesome_agent.settings import Settings


def test_conversation_turn_streams_deltas_before_completion() -> None:
    client = _client(FakeStreamingProvider())
    thread = client.post("/threads", json={"title": "Greeting"}).json()

    response = client.post(
        f"/threads/{thread['id']}/turns",
        json={"content": "hello?"},
    )

    assert response.status_code == 200
    body = response.text
    assert "event: message.delta" in body
    assert "event: message.completed" in body
    assert body.index("event: message.delta") < body.index("event: message.completed")
    messages = client.get(f"/threads/{thread['id']}/messages").json()
    assert [message["content"] for message in messages] == ["hello?", "hello world"]


def test_conversation_turn_error_does_not_persist_assistant_message() -> None:
    client = _client(FailingStreamingProvider())
    thread = client.post("/threads", json={"title": "Failure"}).json()

    response = client.post(
        f"/threads/{thread['id']}/turns",
        json={"content": "hello?"},
    )

    assert response.status_code == 200
    assert "event: error" in response.text
    messages = client.get(f"/threads/{thread['id']}/messages").json()
    assert [message["role"] for message in messages] == ["user"]


def _client(provider: object) -> TestClient:
    repository = InMemoryConversationRepository()
    conversation = ConversationService(
        repository=repository,
        provider_factory=lambda _model: cast(Any, provider),
        default_model="fake-model",
    )
    return TestClient(
        create_app(
            service=cast(Any, object()),
            intake=cast(Any, object()),
            registry=cast(Any, object()),
            settings=Settings(_env_file=None),
            thread_repository=repository,
            conversation_service=conversation,
        )
    )


class FakeStreamingProvider:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
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
