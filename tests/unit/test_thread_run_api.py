from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from awesome_agent.api.app import create_app
from awesome_agent.artifacts.store import ArtifactMetadata
from awesome_agent.domain.enums import RunIntent, RunMode, RunStatus
from awesome_agent.domain.models import Run
from awesome_agent.settings import Settings


def test_create_thread_run_uses_thread_repository_context() -> None:
    intake = FakeRunIntake()
    client = _client(intake)
    repository_id = uuid4()
    thread = client.post(
        "/threads",
        json={"title": "Snake", "repository_id": str(repository_id)},
    ).json()

    response = client.post(
        f"/threads/{thread['id']}/runs",
        json={"goal": "Build snake", "intent": "modifying", "mode": "solo"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["goal"] == "Build snake"
    assert body["repository_id"] == str(repository_id)
    assert intake.calls == [
        {
            "repository_id": repository_id,
            "goal": "Build snake",
            "intent": RunIntent.MODIFYING,
            "mode": RunMode.SOLO,
        }
    ]
    messages = client.get(f"/threads/{thread['id']}/messages").json()
    assert messages[-1]["kind"] == "run"
    assert messages[-1]["run_id"] == body["id"]


def test_create_thread_run_missing_thread_returns_404() -> None:
    client = _client(FakeRunIntake())

    response = client.post(
        f"/threads/{uuid4()}/runs",
        json={"goal": "Build snake"},
    )

    assert response.status_code == 404


def test_create_thread_run_without_repository_context_returns_409() -> None:
    client = _client(FakeRunIntake())
    thread = client.post("/threads", json={"title": "No repo"}).json()

    response = client.post(
        f"/threads/{thread['id']}/runs",
        json={"goal": "Build snake"},
    )

    assert response.status_code == 409
    assert "repository_id" in response.json()["detail"]


def test_create_thread_run_can_bind_repository_context() -> None:
    intake = FakeRunIntake()
    client = _client(intake)
    repository_id = uuid4()
    thread = client.post("/threads", json={"title": "Late repo"}).json()

    response = client.post(
        f"/threads/{thread['id']}/runs",
        json={"goal": "Build snake", "repository_id": str(repository_id)},
    )

    assert response.status_code == 201
    assert response.json()["repository_id"] == str(repository_id)
    assert client.get(f"/threads/{thread['id']}").json()["repository_id"] == str(
        repository_id
    )


def test_list_thread_runs_returns_newest_projection_first() -> None:
    client = _client(FakeRunIntake())
    thread = client.post(
        "/threads",
        json={"title": "Snake", "repository_id": str(uuid4())},
    ).json()

    first = client.post(
        f"/threads/{thread['id']}/runs",
        json={"goal": "First run"},
    ).json()
    second = client.post(
        f"/threads/{thread['id']}/runs",
        json={"goal": "Second run"},
    ).json()
    response = client.get(f"/threads/{thread['id']}/runs")

    assert response.status_code == 200
    assert [item["run_id"] for item in response.json()] == [second["id"], first["id"]]


def test_list_thread_runs_exposes_runtime_status_and_artifacts(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    client = _client(FakeRunIntake(runtime), service=runtime)
    thread = client.post(
        "/threads",
        json={"title": "Snake", "repository_id": str(uuid4())},
    ).json()

    created = client.post(
        f"/threads/{thread['id']}/runs",
        json={"goal": "Build snake"},
    ).json()
    response = client.get(f"/threads/{thread['id']}/runs")

    assert response.status_code == 200
    [projection] = response.json()
    assert projection["run_id"] == created["id"]
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
        self.runs: dict[UUID, Run] = {}
        self.artifacts: dict[UUID, list[ArtifactMetadata]] = {}

    async def get_run(self, run_id: UUID) -> Run:
        return self.runs[run_id]

    async def list_artifacts(self, run_id: UUID) -> list[ArtifactMetadata]:
        return self.artifacts.get(run_id, [])


def _client(
    intake: FakeRunIntake,
    *,
    service: object | None = None,
) -> TestClient:
    return TestClient(
        create_app(
            service=cast(Any, service or object()),
            intake=cast(Any, intake),
            registry=cast(Any, object()),
            settings=Settings(_env_file=None),
        )
    )
