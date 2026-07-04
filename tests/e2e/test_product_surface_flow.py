from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from tests.conversation_projection_fakes import ProjectedConversationRunIntake
from tests.type_helpers import test_settings

from awesome_agent.api.app import create_app
from awesome_agent.artifacts.store import ArtifactMetadata
from awesome_agent.conversation.service import ConversationService
from awesome_agent.domain.enums import (
    AgentKind,
    AgentStatus,
    DispatchStatus,
    EventType,
    ExecutionKind,
    RunIntent,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.runtime.graphs import CONVERSATION_TURN_ROUTE
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
        f"/threads/{thread['id']}/turns/stream",
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


def test_product_surface_continue_does_not_create_user_message(
    tmp_path: Path,
) -> None:
    thread_repository = InMemoryConversationRepository()
    runtime = FakeRuntime(tmp_path)
    intake = ProjectedConversationRunIntake(
        conversations=thread_repository,
        runtime=runtime,
        assistant_content=None,
    )
    conversation_service = ConversationService(
        repository=thread_repository,
        runtime_repository=runtime,
        conversation_run_intake=intake,
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
    thread = client.post("/threads", json={"title": "Continue E2E"}).json()
    original_run_id = asyncio.run(
        _seed_waiting_conversation_run(
            runtime=runtime,
            thread_id=UUID(str(thread["id"])),
            content="original request",
        )
    )
    client.post(
        f"/threads/{thread['id']}/messages",
        json={"role": "user", "content": "original request"},
    )

    with client.stream(
        "POST",
        f"/threads/{thread['id']}/turns/continue/stream",
        json={"expected_run_id": str(original_run_id)},
    ) as response:
        assert response.status_code == 200
        stream_text = response.read().decode()

    messages = client.get(f"/threads/{thread['id']}/messages").json()
    user_messages = [
        message["content"] for message in messages if message["role"] == "user"
    ]

    assert "event: turn.continued" in stream_text
    assert str(original_run_id) in stream_text
    assert intake.created == []
    assert user_messages == ["original request"]


class FakeRuntime(InMemoryRuntimeRepository):
    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.artifacts: dict[UUID, list[ArtifactMetadata]] = {}

    async def list_artifacts(self, run_id: UUID) -> list[ArtifactMetadata]:
        return self.artifacts.get(run_id, [])


async def _seed_waiting_conversation_run(
    *,
    runtime: InMemoryRuntimeRepository,
    thread_id: UUID,
    content: str,
) -> UUID:
    run = Run(
        goal=content,
        intent=RunIntent.CONVERSATION,
        execution_kind=ExecutionKind.CONVERSATION,
        runtime_route=CONVERSATION_TURN_ROUTE,
        status=RunStatus.PAUSED,
        dispatch_status=DispatchStatus.WAITING,
        working_directory=Path("E:/project"),
    )
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake-model",
        status=AgentStatus.READY,
    )
    await runtime.create_run(run, leader)
    await runtime.append_event(
        run_id=run.id,
        event_type=EventType.RUN_CREATED,
        payload={"thread_id": str(thread_id), "goal": content},
        agent_id=leader.id,
    )
    await runtime.append_event(
        run_id=run.id,
        event_type=EventType.RUN_STATUS_CHANGED,
        payload={"status": RunStatus.COMPLETED.value},
        agent_id=leader.id,
    )
    return run.id
