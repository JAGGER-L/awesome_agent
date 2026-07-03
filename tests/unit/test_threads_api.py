from typing import Any, cast

from fastapi.testclient import TestClient

from awesome_agent.api.app import create_app
from awesome_agent.settings import Settings


def test_create_thread_returns_durable_thread() -> None:
    client = TestClient(
        create_app(
            service=cast(Any, object()),
            intake=cast(Any, object()),
            registry=cast(Any, object()),
            settings=Settings(_env_file=None),
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


def test_resume_thread_by_id_or_title() -> None:
    client = _client()
    thread = client.post("/threads", json={"title": "Snake game"}).json()

    by_id = client.get("/threads/resume", params={"query": thread["id"]})
    by_title = client.get("/threads/resume", params={"query": "Snake"})

    assert by_id.status_code == 200
    assert by_id.json()["id"] == thread["id"]
    assert by_title.status_code == 200
    assert by_title.json()["id"] == thread["id"]


def _client() -> TestClient:
    return TestClient(
        create_app(
            service=cast(Any, object()),
            intake=cast(Any, object()),
            registry=cast(Any, object()),
            settings=Settings(_env_file=None),
        )
    )
