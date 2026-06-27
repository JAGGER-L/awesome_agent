import asyncio
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.api.app import create_app
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.models import Repository
from awesome_agent.observability.repository import (
    DurableMetric,
    DurableModelCall,
    DurableSpan,
    InMemoryObservabilityRepository,
    ObservabilityRepository,
)
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
from awesome_agent.runtime.workspaces import WorkspaceRetentionService
from awesome_agent.settings import Settings


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
    observability_repository: ObservabilityRepository | None = None,
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
    worktree_manager = ManagedRunWorktreeManager(tmp_path / "worktrees")
    intake = RunIntakeService(
        registry=registry,
        reservations=reservations,
        runtime=runtime_repository,
        events=event_stream,
        worktrees=worktree_manager,
        allowed_roots=[projects],
        model_resolver=_models(),
    )
    workspace_service = WorkspaceRetentionService(
        runtime_repository=runtime_repository,
        repository_registry=registry,
        worktrees=worktree_manager,
    )
    return (
        TestClient(
            create_app(
                service,
                intake=intake,
                registry=registry,
                validation_repository=validation_repository,
                observability_repository=observability_repository,
                workspace_service=workspace_service,
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
    events = client.get(f"/runs/{run_id}/events/history").json()
    assert len(events) == 3
    assert all(event["trace_id"] == UUID(run_id).hex for event in events)
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


def test_health_endpoint_is_liveness_only(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_api_returns_structured_report(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.get("/ready?profile=api")

    assert response.status_code in {200, 503}
    body = response.json()
    assert body["profile"] == "api"
    assert body["status"] in {"healthy", "degraded", "unhealthy"}
    assert all("name" in check for check in body["checks"])


def test_ready_runtime_returns_503_without_fresh_worker(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.get("/ready?profile=runtime")

    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"


def test_ready_rejects_invalid_profile(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.get("/ready?profile=invalid")

    assert response.status_code == 422


def test_create_app_rejects_public_bind_without_unsafe_consent() -> None:
    try:
        create_app(settings=Settings(api_host="0.0.0.0"))
    except RuntimeError as error:
        assert "non-loopback" in str(error)
    else:
        raise AssertionError("public bind should be rejected")


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


def test_workspace_endpoints_list_preview_and_reject_force_without_reason(
    tmp_path: Path,
) -> None:
    client, repository = _client(tmp_path)
    created = client.post(
        "/runs",
        json={
            "repository_id": str(repository.id),
            "goal": "Inspect project",
            "intent": "read_only",
        },
    )
    run_id = created.json()["id"]

    listed = client.get("/workspaces")
    preview = client.post(
        "/workspaces/cleanup-preview",
        json={"run_id": run_id},
    )
    rejected = client.post(
        "/workspaces/cleanup",
        json={"run_id": run_id, "force": True},
    )

    assert listed.status_code == 200
    assert listed.json()[0]["run_id"] == run_id
    assert preview.status_code == 200
    assert preview.json()[0]["status"] == "blocked_active_run"
    assert rejected.status_code == 422
    assert "reason" in rejected.json()["detail"]


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


def test_observability_endpoints_return_run_trace_metrics_and_model_calls(
    tmp_path: Path,
) -> None:
    observability = InMemoryObservabilityRepository()
    client, repository = _client(
        tmp_path,
        observability_repository=observability,
    )
    created = client.post(
        "/runs",
        json={
            "repository_id": str(repository.id),
            "goal": "Inspect project",
            "intent": "read_only",
        },
    )
    run_id = UUID(created.json()["id"])

    async def record() -> None:
        await observability.record_span(
            DurableSpan(
                run_id=run_id,
                trace_id=run_id.hex,
                span_id="0000000000000001",
                parent_span_id=None,
                name="run.execute",
                category="run",
                status="completed",
            )
        )
        await observability.record_metric(
            DurableMetric(
                run_id=run_id,
                name="run.duration_ms",
                value=42,
                unit="ms",
            )
        )
        await observability.record_model_call(
            DurableModelCall(
                run_id=run_id,
                agent_id=None,
                turn=1,
                provider="deepseek",
                model="deepseek-v4-flash",
                status="completed",
                stop_reason="completed",
                input_tokens=5,
                output_tokens=7,
                latency_ms=42,
                trace_id=run_id.hex,
                span_id="0000000000000002",
            )
        )

    asyncio.run(record())

    trace = client.get(f"/runs/{run_id}/trace")
    metrics = client.get(f"/runs/{run_id}/metrics")
    model_calls = client.get(f"/runs/{run_id}/model-calls")

    assert trace.status_code == 200
    assert trace.json()[0]["name"] == "run.execute"
    assert metrics.status_code == 200
    assert metrics.json()[0]["name"] == "run.duration_ms"
    assert model_calls.status_code == 200
    assert model_calls.json()[0]["model"] == "deepseek-v4-flash"


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


def test_team_run_uses_team_graph_and_starts_with_leader_only(
    tmp_path: Path,
) -> None:
    client, repository = _client(tmp_path)

    response = client.post(
        "/runs",
        json={
            "repository_id": str(repository.id),
            "goal": "Implement backend and verify it",
            "intent": "modifying",
            "mode": "team",
        },
    )

    assert response.status_code == 201
    body = response.json()
    run_id = body["id"]
    assert body["mode"] == "team"
    assert body["graph_name"] == "team-coding"
    assert body["graph_version"] == 1
    agents = client.get(f"/runs/{run_id}/agents").json()
    assert len(agents) == 1
    assert agents[0]["kind"] == "leader"
    todos = client.get(f"/runs/{run_id}/todos").json()
    assert todos == []


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
