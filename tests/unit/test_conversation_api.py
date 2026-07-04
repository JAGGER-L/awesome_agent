from __future__ import annotations

import asyncio
import json
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
        f"/threads/{thread['id']}/turns/stream",
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
        f"/threads/{thread['id']}/turns/stream",
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
        f"/threads/{thread['id']}/turns/stream",
        json={
            "content": "hello?",
            "model": "deepseek-v4-flash",
            "thinking_mode": "off",
            "memory": {"local_enabled": True, "provider": "mem0"},
            "skill_ids": ["repository-inspection"],
        },
    )

    assert response.status_code == 200
    messages = client.get(f"/threads/{thread['id']}/messages").json()
    user_options = messages[0]["metadata"]["turn_options"]
    assert user_options == {
        "model": "deepseek-v4-flash",
        "thinking": "off",
        "memory": {"local_enabled": False, "provider": None},
        "skill_ids": ["repository-inspection"],
        "attachment_ids": [],
    }


def test_conversation_turn_rejects_unknown_model_before_run_creation() -> None:
    repository = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    conversation = ConversationService(
        repository=repository,
        runtime_repository=runtime,
        conversation_run_intake=ProjectedConversationRunIntake(
            conversations=repository,
            runtime=runtime,
        ),
        default_model="deepseek-v4-pro",
        event_poll_interval=0,
    )
    client = TestClient(
        create_app(
            service=cast(Any, object()),
            intake=cast(Any, object()),
            registry=cast(Any, object()),
            settings=test_settings(),
            thread_repository=repository,
            conversation_service=conversation,
        )
    )
    thread = client.post(
        "/threads",
        json={"title": "Invalid model", "context_path": "E:/project"},
    ).json()

    response = client.post(
        f"/threads/{thread['id']}/turns/stream",
        json={"content": "hello?", "model": "gpt-4o"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_model"
    assert client.get(f"/threads/{thread['id']}/messages").json() == []
    assert len(asyncio.run(runtime.list_runs())) == 0


def test_continue_turn_stream_uses_after_sequence_for_runtime_catchup() -> None:
    client = _client()
    thread = client.post(
        "/threads",
        json={"title": "Catchup", "context_path": "E:/project"},
    ).json()
    first_response = client.post(
        f"/threads/{thread['id']}/turns/stream",
        json={"content": "hello?"},
    )
    assert first_response.status_code == 200
    first_events = _sse_events(first_response.text)
    run_id = next(
        event["run_id"] for event in first_events if event["event"] == "turn.started"
    )
    first_delta = next(
        event for event in first_events if event["event"] == "message.delta"
    )
    first_runtime_sequence = first_delta["runtime_sequence"]
    assert isinstance(first_runtime_sequence, int)

    response = client.post(
        f"/threads/{thread['id']}/turns/continue/stream",
        json={
            "expected_run_id": run_id,
            "after_sequence": first_runtime_sequence,
        },
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert all(
        event.get("runtime_sequence") is None
        or (
            isinstance(event["runtime_sequence"], int)
            and event["runtime_sequence"] > first_runtime_sequence
        )
        for event in events
    )
    assert events[-1]["event"] in {"turn.completed", "error"}


def _sse_events(body: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for block in body.strip().split("\n\n"):
        data_lines = [
            line.removeprefix("data:").strip()
            for line in block.splitlines()
            if line.startswith("data:")
        ]
        if data_lines:
            events.append(json.loads("\n".join(data_lines)))
    return events


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
