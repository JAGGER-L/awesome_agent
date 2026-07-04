from collections.abc import AsyncIterator
from time import sleep
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

from fastapi.testclient import TestClient
from tests.type_helpers import test_settings

from awesome_agent.api.app import create_app
from awesome_agent.conversation.events import ConversationStreamEvent


def test_create_thread_returns_durable_thread() -> None:
    client = TestClient(
        create_app(
            service=cast(Any, object()),
            intake=cast(Any, object()),
            registry=cast(Any, object()),
            settings=test_settings(),
        )
    )

    response = client.post("/threads", json={"title": "Snake game"})

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Snake game"
    assert body["id"]
    assert body["created_at"]
    assert body["host_workspace_path"].endswith("/workspace") or body[
        "host_workspace_path"
    ].endswith("\\workspace")
    assert body["logical_workspace_path"] == "/mnt/user-data/workspace/"


def test_create_thread_accepts_context_metadata() -> None:
    client = _client()

    response = client.post(
        "/threads",
        json={
            "title": "Snake game",
            "context_kind": "repo",
            "context_path": "E:/games/snake",
            "default_model": "deepseek-v4-pro",
            "sandbox_profile": "local",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["context_kind"] == "repo"
    assert body["context_path"] == "E:/games/snake"
    assert body["default_model"] == "deepseek-v4-pro"
    assert body["sandbox_profile"] == "local"


def test_list_threads_returns_newest_updated_first() -> None:
    client = _client()
    first = client.post("/threads", json={"title": "First"}).json()
    second = client.post("/threads", json={"title": "Second"}).json()
    sleep(0.02)
    client.post(
        f"/threads/{first['id']}/messages",
        json={"role": "user", "content": "update first"},
    )

    response = client.get("/threads")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [first["id"], second["id"]]


def test_list_threads_includes_latest_changed_file_summary() -> None:
    client = _client()
    thread = client.post("/threads", json={"title": "Snake game"}).json()
    client.post(
        f"/threads/{thread['id']}/messages",
        json={
            "role": "assistant",
            "content": "Done.",
            "metadata": {
                "changed_files": [
                    {
                        "path": "/mnt/user-data/workspace/snake.html",
                        "status": "created",
                    }
                ]
            },
        },
    )

    response = client.get("/threads")

    assert response.status_code == 200
    [summary] = response.json()
    assert summary["changed_file_count"] == 1
    assert summary["latest_changed_files"] == [
        {
            "path": "/mnt/user-data/workspace/snake.html",
            "status": "created",
            "display_path": "snake.html",
        }
    ]


def test_get_thread_returns_created_thread() -> None:
    client = _client()
    created = client.post("/threads", json={"title": "Snake game"}).json()

    response = client.get(f"/threads/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_update_thread_settings_returns_updated_thread() -> None:
    client = _client()
    created = client.post("/threads", json={"title": "Settings"}).json()

    response = client.patch(
        f"/threads/{created['id']}/settings",
        json={
            "default_model": "deepseek-v4-flash",
            "thinking_mode": "off",
            "local_memory_enabled": True,
            "provider_memory": "mem0",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["default_model"] == "deepseek-v4-flash"
    assert body["thinking_mode"] == "off"
    assert body["local_memory_enabled"] is True
    assert body["provider_memory"] == "mem0"


def test_update_thread_settings_rejects_unknown_default_model() -> None:
    client = _client()
    created = client.post("/threads", json={"title": "Settings"}).json()

    response = client.patch(
        f"/threads/{created['id']}/settings",
        json={"default_model": "gpt-4o"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "unsupported_model"
    assert "gpt-4o" in body["message"]
    unchanged = client.get(f"/threads/{created['id']}").json()
    assert unchanged["default_model"] is None


def test_append_and_list_thread_messages() -> None:
    client = _client()
    thread = client.post("/threads", json={"title": "Snake game"}).json()

    first = client.post(
        f"/threads/{thread['id']}/messages",
        json={"role": "user", "content": "Build snake."},
    )
    second = client.post(
        f"/threads/{thread['id']}/messages",
        json={
            "role": "assistant",
            "content": "I can help.",
            "kind": "message",
            "metadata": {"source": "test"},
        },
    )
    response = client.get(f"/threads/{thread['id']}/messages")

    assert first.status_code == 200
    assert second.status_code == 200
    assert response.status_code == 200
    messages = response.json()
    assert [item["sequence"] for item in messages] == [1, 2]
    assert [item["content"] for item in messages] == ["Build snake.", "I can help."]
    assert messages[1]["metadata"] == {"source": "test"}


def test_threads_resolve_replaces_threads_resume() -> None:
    client = _client()
    thread = client.post("/threads", json={"title": "Snake game"}).json()

    by_id = client.get("/threads/resolve", params={"query": thread["id"]})
    by_title = client.get("/threads/resolve", params={"query": "Snake"})
    old = client.get("/threads/resume", params={"query": "Snake"})

    assert by_id.status_code == 200
    assert by_id.json()["id"] == thread["id"]
    assert by_title.status_code == 200
    assert by_title.json()["id"] == thread["id"]
    assert old.status_code == 404


def test_old_turns_endpoint_is_removed() -> None:
    client = _client()
    thread = client.post("/threads", json={"title": "Chat"}).json()

    response = client.post(f"/threads/{thread['id']}/turns", json={"content": "hi"})

    assert response.status_code == 404


def test_continue_stream_rejects_content() -> None:
    client = _client()
    thread = client.post("/threads", json={"title": "Chat"}).json()

    response = client.post(
        f"/threads/{thread['id']}/turns/continue/stream",
        json={"content": "continue"},
    )

    assert response.status_code == 422


def test_continue_stream_without_resumable_run_returns_structured_conflict() -> None:
    client = _client()
    thread = client.post("/threads", json={"title": "Chat"}).json()

    response = client.post(f"/threads/{thread['id']}/turns/continue/stream", json={})

    assert response.status_code == 409
    assert response.json()["code"] == "no_resumable_turn"


def test_continue_stream_expected_run_mismatch_returns_structured_conflict() -> None:
    thread_id = UUID("00000000-0000-0000-0000-000000000001")
    current_run_id = UUID("00000000-0000-0000-0000-000000000002")
    expected_run_id = UUID("00000000-0000-0000-0000-000000000003")
    client = _client(conversation_service=_FakeConversationService(current_run_id))

    response = client.post(
        f"/threads/{thread_id}/turns/continue/stream",
        json={"expected_run_id": str(expected_run_id)},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "resumable_run_changed"


def test_top_level_run_write_endpoints_are_removed() -> None:
    client = _client()
    run_id = "00000000-0000-0000-0000-000000000001"
    approval_id = "00000000-0000-0000-0000-000000000002"

    cancel = client.post(f"/runs/{run_id}/cancel")
    resume = client.post(f"/runs/{run_id}/resume")
    approval = client.post(
        f"/runs/{run_id}/approvals/{approval_id}",
        json={"approved": True},
    )

    assert cancel.status_code in {404, 405}
    assert resume.status_code in {404, 405}
    assert approval.status_code in {404, 405}


class _FakeConversationService:
    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id

    async def latest_resumable_thread_run(
        self,
        thread_id: UUID,
    ) -> SimpleNamespace:
        return SimpleNamespace(id=self.run_id)

    async def continuable_thread_run(
        self,
        thread_id: UUID,
        *,
        expected_run_id: UUID | None = None,
    ) -> SimpleNamespace | None:
        if expected_run_id is not None and expected_run_id != self.run_id:
            return None
        return SimpleNamespace(id=self.run_id)

    async def continue_turn(
        self,
        *,
        thread_id: UUID,
        expected_run_id: UUID | None = None,
        after_sequence: int = 0,
    ) -> AsyncIterator[ConversationStreamEvent]:
        if False:
            yield  # pragma: no cover


def _client(conversation_service: Any | None = None) -> TestClient:
    return TestClient(
        create_app(
            service=cast(Any, object()),
            intake=cast(Any, object()),
            registry=cast(Any, object()),
            settings=test_settings(),
            conversation_service=cast(Any, conversation_service),
        )
    )
