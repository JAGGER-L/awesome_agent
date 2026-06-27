import asyncio
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.api.app import create_app
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.models import Repository
from awesome_agent.persistence.validation import (
    DurableValidationGateResult,
    DurableValidationReport,
    InMemoryValidationRepository,
    ValidationRepository,
)
from awesome_agent.repositories.registry import InMemoryRepositoryRegistry
from awesome_agent.repositories.reservations import (
    InMemoryIntakeReservationStore,
)
from awesome_agent.repositories.worktrees import ManagedRunWorktreeManager
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.intake import RunIntakeService
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.service import RuntimeService


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="deepseek-v4-pro",
        teammate_model="deepseek-v4-flash",
        verifier_model="deepseek-v4-flash",
        subagent_model="deepseek-v4-flash",
    )


def _git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=path,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def _client(
    tmp_path: Path,
    *,
    validation_repository: ValidationRepository | None = None,
) -> tuple[TestClient, Repository]:
    projects = tmp_path / "projects"
    repository_path = projects / "repository"
    repository_path.mkdir(parents=True)
    _git(repository_path, "init")
    _git(repository_path, "config", "user.email", "test@example.com")
    _git(repository_path, "config", "user.name", "Test")
    (repository_path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(repository_path, "add", "README.md")
    _git(repository_path, "commit", "-m", "Initial")
    repository = Repository(
        root=repository_path.resolve(),
        display_name="repository",
        git_common_dir=(repository_path / ".git").resolve(),
        default_branch=_git(repository_path, "branch", "--show-current"),
    )
    registry = InMemoryRepositoryRegistry()
    asyncio.run(registry.upsert(repository))
    reservations = InMemoryIntakeReservationStore()
    runtime_repository = InMemoryRuntimeRepository(reservations)
    event_stream = EventStream()
    service = RuntimeService(
        repository=runtime_repository,
        events=event_stream,
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
    )
    intake = RunIntakeService(
        registry=registry,
        reservations=reservations,
        runtime=runtime_repository,
        events=event_stream,
        worktrees=ManagedRunWorktreeManager(tmp_path / "worktrees"),
        allowed_roots=[projects],
        model_resolver=_models(),
    )
    return (
        TestClient(
            create_app(
                service,
                intake=intake,
                registry=registry,
                validation_repository=validation_repository,
            )
        ),
        repository,
    )


def test_create_inspect_and_cancel_run(tmp_path: Path) -> None:
    client, repository = _client(tmp_path)

    created = client.post(
        "/runs",
        json={
            "repository_id": str(repository.id),
            "goal": "Implement feature",
            "intent": "read_only",
        },
    )
    assert created.status_code == 201
    run_id = created.json()["id"]

    run = client.get(f"/runs/{run_id}").json()
    assert run["status"] == "created"
    assert run["dispatch_status"] == "queued"
    assert run["intent"] == "read_only"
    dispatch = client.get(f"/runs/{run_id}/dispatch").json()
    assert dispatch["status"] == "queued"
    assert dispatch["worker_id"] is None
    assert dispatch["fencing_token"] == 0
    agents = client.get(f"/runs/{run_id}/agents").json()
    assert len(agents) == 1
    assert agents[0]["model"] == "deepseek-v4-pro"
    assert agents[0]["revision"] == 1
    assert agents[0]["updated_at"] is not None
    assert len(client.get(f"/runs/{run_id}/events/history").json()) == 3
    todos = client.get(f"/runs/{run_id}/todos").json()
    assert len(todos) == 1
    assert todos[0]["status"] == "in_progress"

    cancelled = client.post(f"/runs/{run_id}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["dispatch_status"] == "terminal"

    resumed = client.post(f"/runs/{run_id}/resume")
    assert resumed.status_code == 409

    approval_id = uuid4()
    decided = client.post(
        f"/runs/{run_id}/approvals/{approval_id}",
        json={"approved": True},
    )
    assert decided.status_code == 200
    assert len(client.get(f"/runs/{run_id}/approvals").json()) == 1


def test_missing_run_returns_404(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.get("/runs/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


def test_repository_endpoints_and_path_injection_rejection(
    tmp_path: Path,
) -> None:
    client, repository = _client(tmp_path)

    listed = client.get("/repositories")
    fetched = client.get(f"/repositories/{repository.id}")
    injected = client.post(
        "/runs",
        json={
            "repository_id": str(repository.id),
            "repository_path": str(repository.root),
            "goal": "Injected path",
        },
    )

    assert listed.status_code == 200
    assert listed.json()[0]["id"] == str(repository.id)
    assert fetched.status_code == 200
    assert fetched.json()["root"] == str(repository.root)
    assert injected.status_code == 422


def test_runtime_probe_has_explicit_execution_identity(tmp_path: Path) -> None:
    client, repository = _client(tmp_path)

    response = client.post(
        "/runtime/probes",
        json={"repository_id": str(repository.id)},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["execution_kind"] == "runtime_probe"
    assert body["graph_name"] == "runtime-probe"
    assert body["graph_version"] == 1


def test_modifying_run_has_executable_graph_route(tmp_path: Path) -> None:
    client, repository = _client(tmp_path)

    response = client.post(
        "/runs",
        json={
            "repository_id": str(repository.id),
            "goal": "Fix bug",
            "intent": "modifying",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["graph_name"] == "solo-modifying"
    assert body["graph_version"] == 1
    assert body["dispatch_status"] == "queued"


def test_verification_endpoint_returns_durable_validation_reports(
    tmp_path: Path,
) -> None:
    validation = InMemoryValidationRepository()
    client, repository = _client(tmp_path, validation_repository=validation)
    created = client.post(
        "/runs",
        json={
            "repository_id": str(repository.id),
            "goal": "Fix bug",
            "intent": "modifying",
        },
    )
    run_id = UUID(created.json()["id"])
    report = DurableValidationReport(
        run_id=run_id,
        agent_id=None,
        attempt=1,
        status="failed",
        summary="pytest failed",
    )
    stored = asyncio.run(
        validation.record_report(
            report,
            gates=[
                DurableValidationGateResult(
                    report_id=report.id,
                    run_id=run_id,
                    gate_id="pytest",
                    name="Pytest",
                    command=["pytest", "-q"],
                    required=True,
                    status="failed",
                    exit_code=1,
                    failure_kind="command_failed",
                    stdout_summary="1 failed",
                )
            ],
        )
    )

    body = client.get(f"/runs/{run_id}/verification").json()

    assert body[0]["id"] == str(stored.id)
    assert body[0]["status"] == "failed"
    assert body[0]["gates"][0]["gate_id"] == "pytest"
    assert body[0]["gates"][0]["failure_kind"] == "command_failed"
