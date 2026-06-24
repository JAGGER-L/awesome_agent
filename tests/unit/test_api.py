from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.api.app import create_app
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.service import RuntimeService


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="deepseek-v4-pro",
        teammate_model="deepseek-v4-flash",
        verifier_model="deepseek-v4-flash",
        subagent_model="deepseek-v4-flash",
    )


def _client(tmp_path: Path) -> TestClient:
    service = RuntimeService(
        repository=InMemoryRuntimeRepository(),
        events=EventStream(),
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
    )
    return TestClient(create_app(service))


def test_create_inspect_and_cancel_run(tmp_path: Path) -> None:
    client = _client(tmp_path)

    created = client.post("/runs", json={"goal": "Implement feature"})
    assert created.status_code == 201
    run_id = created.json()["id"]

    assert client.get(f"/runs/{run_id}").json()["status"] == "running"
    agents = client.get(f"/runs/{run_id}/agents").json()
    assert len(agents) == 1
    assert agents[0]["model"] == "deepseek-v4-pro"
    assert len(client.get(f"/runs/{run_id}/events/history").json()) == 2

    cancelled = client.post(f"/runs/{run_id}/cancel")
    assert cancelled.json()["status"] == "cancelled"

    resumed = client.post(f"/runs/{run_id}/resume")
    assert resumed.json()["status"] == "running"

    approval_id = uuid4()
    decided = client.post(
        f"/runs/{run_id}/approvals/{approval_id}",
        json={"approved": True},
    )
    assert decided.status_code == 200
    assert len(client.get(f"/runs/{run_id}/approvals").json()) == 1


def test_missing_run_returns_404(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/runs/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
