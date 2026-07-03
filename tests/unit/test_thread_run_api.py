from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from tests.type_helpers import test_settings

from awesome_agent.api.app import create_app
from awesome_agent.artifacts.store import ArtifactMetadata
from awesome_agent.domain.enums import (
    AgentKind,
    AgentStatus,
    DispatchStatus,
    EventType,
    ExecutionKind,
    RunIntent,
    RunMode,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.runtime.graphs import CONVERSATION_TURN_ROUTE
from awesome_agent.runtime.repository import InMemoryRuntimeRepository


def test_post_thread_runs_is_removed_from_product_api() -> None:
    client = _client(FakeRunIntake())
    thread = client.post("/threads", json={"title": "Chat"}).json()

    response = client.post(
        f"/threads/{thread['id']}/runs",
        json={"goal": "Build snake"},
    )

    assert response.status_code == 405


def test_list_thread_runs_returns_newest_projection_first() -> None:
    intake = FakeRunIntake()
    client = _client(intake)
    thread = client.post(
        "/threads",
        json={"title": "Snake", "repository_id": str(uuid4())},
    ).json()

    first = _record_thread_run(client, thread["id"], "First run")
    second = _record_thread_run(client, thread["id"], "Second run")
    response = client.get(f"/threads/{thread['id']}/runs")

    assert response.status_code == 200
    assert [item["run_id"] for item in response.json()] == [
        str(second.id),
        str(first.id),
    ]


def test_list_thread_runs_exposes_runtime_status_and_artifacts(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    client = _client(FakeRunIntake(runtime), service=runtime)
    thread = client.post(
        "/threads",
        json={"title": "Snake", "repository_id": str(uuid4())},
    ).json()

    created = _record_thread_run(client, thread["id"], "Build snake")
    response = client.get(f"/threads/{thread['id']}/runs")

    assert response.status_code == 200
    [projection] = response.json()
    assert projection["run_id"] == str(created.id)
    assert projection["status"] == "completed"
    assert projection["result_text"] == "done"
    assert projection["artifacts"][0]["path"].endswith("snake.html")


class FakeRunIntake:
    def __init__(self, runtime: FakeRuntime | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.runtime = runtime

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
            goal=goal,
            repository_id=repository_id,
            intent=intent,
            mode=mode,
            status=RunStatus.CREATED,
        )
        if self.runtime is not None:
            self.runtime.runs[run.id] = run.model_copy(
                update={"status": RunStatus.COMPLETED, "result_text": "done"}
            )
            self.runtime.artifacts[run.id] = [
                ArtifactMetadata(
                    run_id=run.id,
                    artifact_type="html",
                    path=self.runtime.root / "snake.html",
                    sha256="abc",
                    size=42,
                    mime_type="text/html",
                    summary="Snake game",
                )
            ]
        return run


class FakeRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.repository = InMemoryRuntimeRepository()
        self.runs: dict[UUID, Run] = {}
        self.artifacts: dict[UUID, list[ArtifactMetadata]] = {}

    async def get_run(self, run_id: UUID) -> Run:
        if run_id in self.runs:
            return self.runs[run_id]
        return await self.repository.get_run(run_id)

    async def list_artifacts(self, run_id: UUID) -> list[ArtifactMetadata]:
        return self.artifacts.get(run_id, [])


def _client(
    intake: FakeRunIntake,
    *,
    service: object | None = None,
) -> TestClient:
    runtime_service = service or FakeRuntime(Path.cwd())
    return TestClient(
        create_app(
            service=cast(Any, runtime_service),
            intake=cast(Any, intake),
            registry=cast(Any, object()),
            settings=test_settings(),
        )
    )


def _record_thread_run(
    client: TestClient,
    thread_id: str,
    goal: str,
) -> Run:
    runtime = cast(FakeRuntime, cast(Any, client.app).state.runtime)
    run = Run(
        goal=goal,
        intent=RunIntent.CONVERSATION,
        execution_kind=ExecutionKind.CONVERSATION,
        runtime_route=CONVERSATION_TURN_ROUTE,
        status=RunStatus.COMPLETED,
        dispatch_status=DispatchStatus.TERMINAL,
        result_text="done",
    )
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake-model",
        status=AgentStatus.READY,
    )
    asyncio.run(runtime.repository.create_run(run, leader))
    asyncio.run(
        runtime.repository.append_event(
            run_id=run.id,
            event_type=EventType.RUN_CREATED,
            payload={"thread_id": thread_id, "goal": goal},
            agent_id=leader.id,
        )
    )
    runtime.artifacts[run.id] = [
        ArtifactMetadata(
            run_id=run.id,
            artifact_type="html",
            path=runtime.root / "snake.html",
            sha256="abc",
            size=42,
            mime_type="text/html",
            summary="Snake game",
        )
    ]
    return run
