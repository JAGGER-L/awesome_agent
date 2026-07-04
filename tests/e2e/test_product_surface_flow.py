from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from tests.conversation_projection_fakes import ProjectedConversationRunIntake
from tests.type_helpers import test_settings

from awesome_agent.api.app import create_app
from awesome_agent.artifacts.store import ArtifactMetadata
from awesome_agent.conversation.service import ConversationService
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.runtime.repository import InMemoryRuntimeRepository


def test_product_surface_thread_turn_run_and_artifact_flow(tmp_path: Path) -> None:
    thread_repository = InMemoryConversationRepository()
    runtime = FakeRuntime(tmp_path)
    conversation_service = ConversationService(
        repository=thread_repository,
        runtime_repository=runtime,
        conversation_run_intake=ProjectedConversationRunIntake(
            conversations=thread_repository,
            runtime=runtime,
            assistant_content="Here is your tiny HTML snake game.",
            text_deltas=("Here is your tiny ", "HTML snake game."),
            usage={"input_tokens": 2, "output_tokens": 7},
        ),
        default_model="fake-model",
        event_poll_interval=0,
    )
    client = TestClient(
        create_app(
            service=cast(Any, runtime),
            intake=cast(Any, object()),
            registry=cast(Any, object()),
            settings=test_settings(),
            thread_repository=thread_repository,
            conversation_service=conversation_service,
        )
    )
    repository_id = uuid4()

    thread_response = client.post(
        "/threads",
        json={"title": "Snake E2E", "repository_id": str(repository_id)},
    )
    assert thread_response.status_code == 200
    thread = thread_response.json()

    with client.stream(
        "POST",
        f"/threads/{thread['id']}/turns",
        json={"content": "Say hello"},
    ) as response:
        assert response.status_code == 200
        stream_text = response.read().decode()
    assert "event: message.delta" in stream_text
    assert "event: message.completed" in stream_text
    messages = client.get(f"/threads/{thread['id']}/messages").json()
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[-1]["content"] == "Here is your tiny HTML snake game."

    runs_response = client.get(f"/threads/{thread['id']}/runs")
    assert runs_response.status_code == 200
    [run] = runs_response.json()
    assert run["goal"] == "Say hello"
    run_id = UUID(str(run["run_id"]))
    runtime.artifacts[run_id] = [
        ArtifactMetadata(
            run_id=run_id,
            artifact_type="html",
            path=runtime.root / "snake.html",
            sha256="abc",
            size=42,
            mime_type="text/html",
            summary="Simple snake game",
        )
    ]

    artifacts = client.get(f"/threads/{thread['id']}/artifacts")
    assert artifacts.status_code == 200
    [artifact] = artifacts.json()["items"]
    assert artifact["run_id"] == str(run_id)
    assert artifact["path"].endswith("snake.html")
    assert artifact["mime_type"] == "text/html"


class FakeRuntime(InMemoryRuntimeRepository):
    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.artifacts: dict[UUID, list[ArtifactMetadata]] = {}

    async def list_artifacts(self, run_id: UUID) -> list[ArtifactMetadata]:
        return self.artifacts.get(run_id, [])
