from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient
from tests.conversation_projection_fakes import ProjectedConversationRunIntake
from tests.type_helpers import test_settings

from awesome_agent.api.app import create_app
from awesome_agent.conversation.service import ConversationService
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.runtime.repository import InMemoryRuntimeRepository


def test_conversation_turn_streams_deltas_before_completion() -> None:
    client = _client()
    thread = client.post(
        "/threads",
        json={"title": "Greeting", "context_path": "E:/project"},
    ).json()

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
    client = _client(fail=True, assistant_content=None, text_deltas=())
    thread = client.post(
        "/threads",
        json={"title": "Failure", "context_path": "E:/project"},
    ).json()

    response = client.post(
        f"/threads/{thread['id']}/turns",
        json={"content": "hello?"},
    )

    assert response.status_code == 200
    assert "event: error" in response.text
    messages = client.get(f"/threads/{thread['id']}/messages").json()
    assert [message["role"] for message in messages] == ["user"]


def test_conversation_turn_accepts_runtime_options() -> None:
    repository = InMemoryConversationRepository()
    client = _client(repository=repository)
    thread = client.post(
        "/threads",
        json={"title": "Options", "context_path": "E:/project"},
    ).json()

    response = client.post(
        f"/threads/{thread['id']}/turns",
        json={
            "content": "hello?",
            "model": "alternate-model",
            "thinking_mode": "off",
            "memory": {"local_enabled": True, "provider": "mem0"},
            "skill_ids": ["repository-inspection"],
        },
    )

    assert response.status_code == 200
    messages = client.get(f"/threads/{thread['id']}/messages").json()
    user_options = messages[0]["metadata"]["turn_options"]
    assert user_options == {
        "model": "alternate-model",
        "thinking": "off",
        "memory": {"local_enabled": True, "provider": "mem0"},
        "skill_ids": ["repository-inspection"],
    }


def _client(
    *,
    repository: InMemoryConversationRepository | None = None,
    assistant_content: str | None = "hello world",
    text_deltas: tuple[str, ...] = ("hello", " world"),
    fail: bool = False,
) -> TestClient:
    repository = repository or InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    conversation = ConversationService(
        repository=repository,
        runtime_repository=runtime,
        conversation_run_intake=ProjectedConversationRunIntake(
            conversations=repository,
            runtime=runtime,
            assistant_content=assistant_content,
            text_deltas=text_deltas,
            fail=fail,
        ),
        default_model="fake-model",
        event_poll_interval=0,
    )
    return TestClient(
        create_app(
            service=cast(Any, object()),
            intake=cast(Any, object()),
            registry=cast(Any, object()),
            settings=test_settings(),
            thread_repository=repository,
            conversation_service=conversation,
        )
    )
