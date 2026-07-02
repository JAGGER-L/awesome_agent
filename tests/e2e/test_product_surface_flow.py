from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from awesome_agent.api.app import create_app
from awesome_agent.artifacts.store import ArtifactMetadata
from awesome_agent.conversation.service import ConversationService
from awesome_agent.domain.enums import RunIntent, RunMode, RunStatus
from awesome_agent.domain.models import Run
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.provider import ModelProvider
from awesome_agent.modeling.stream import ModelStreamEvent, TextDelta, TurnCompleted
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, ModelUsage, StopReason
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.settings import Settings


def test_product_surface_thread_turn_run_and_artifact_flow(tmp_path: Path) -> None:
    thread_repository = InMemoryConversationRepository()
    runtime = FakeRuntime(tmp_path)
    intake = FakeRunIntake(runtime)
    conversation_service = ConversationService(
        repository=thread_repository,
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    client = TestClient(
        create_app(
            service=cast(Any, runtime),
            intake=cast(Any, intake),
            registry=cast(Any, object()),
            settings=Settings(_env_file=None),
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

    run_response = client.post(
        f"/threads/{thread['id']}/runs",
        json={"goal": "Create a simple HTML snake game"},
    )
    assert run_response.status_code == 201
    run = run_response.json()
    assert run["repository_id"] == str(repository_id)
    assert intake.calls == [
        {
            "repository_id": repository_id,
            "goal": "Create a simple HTML snake game",
            "intent": RunIntent.MODIFYING,
            "mode": RunMode.SOLO,
        }
    ]

    artifacts = client.get(f"/threads/{thread['id']}/artifacts")
    assert artifacts.status_code == 200
    [artifact] = artifacts.json()["items"]
    assert artifact["run_id"] == run["id"]
    assert artifact["path"].endswith("snake.html")
    assert artifact["mime_type"] == "text/html"


class FakeProvider(ModelProvider):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        assert request.messages[-1].content == "Say hello"
        yield TextDelta(text="Here is your tiny ")
        yield TextDelta(text="HTML snake game.")
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(
                    content="Here is your tiny HTML snake game."
                ),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
                usage=ModelUsage(input_tokens=2, output_tokens=7),
            )
        )

    async def complete(self, request: ModelRequest) -> ModelTurn:
        async for event in self.stream(request):
            if isinstance(event, TurnCompleted):
                return event.turn
        raise AssertionError("FakeProvider did not complete.")


class FakeRunIntake:
    def __init__(self, runtime: FakeRuntime) -> None:
        self.runtime = runtime
        self.calls: list[dict[str, object]] = []

    async def create_run(
        self,
        *,
        repository_id: UUID,
        goal: str,
        intent: RunIntent,
        mode: RunMode = RunMode.SOLO,
    ) -> Run:
        self.calls.append(
            {
                "repository_id": repository_id,
                "goal": goal,
                "intent": intent,
                "mode": mode,
            }
        )
        run = Run(
            repository_id=repository_id,
            goal=goal,
            intent=intent,
            mode=mode,
            status=RunStatus.COMPLETED,
            result_text="created snake.html",
        )
        self.runtime.runs[run.id] = run
        self.runtime.artifacts[run.id] = [
            ArtifactMetadata(
                run_id=run.id,
                artifact_type="html",
                path=self.runtime.root / "snake.html",
                sha256="abc",
                size=42,
                mime_type="text/html",
                summary="Simple snake game",
            )
        ]
        return run


class FakeRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.runs: dict[UUID, Run] = {}
        self.artifacts: dict[UUID, list[ArtifactMetadata]] = {}

    async def get_run(self, run_id: UUID) -> Run:
        return self.runs[run_id]

    async def list_artifacts(self, run_id: UUID) -> list[ArtifactMetadata]:
        return self.artifacts.get(run_id, [])
