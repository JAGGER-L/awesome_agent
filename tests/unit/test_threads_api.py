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


def test_get_thread_returns_created_thread() -> None:
    client = TestClient(
        create_app(
            service=cast(Any, object()),
            intake=cast(Any, object()),
            registry=cast(Any, object()),
            settings=Settings(_env_file=None),
        )
    )
    created = client.post("/threads", json={"title": "Snake game"}).json()

    response = client.get(f"/threads/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
