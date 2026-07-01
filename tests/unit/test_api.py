import asyncio
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.api.app import create_app
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import AgentKind, EventType, RunMode, RunStatus
from awesome_agent.domain.models import Agent, Repository, Run
from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionHealthSnapshot,
    ExtensionSourceSnapshot,
    ExtensionToolInventoryItem,
)
from awesome_agent.observability.facade import ObservabilityFacade
from awesome_agent.observability.repository import (
    DurableMetric,
    DurableModelCall,
    DurableSpan,
    InMemoryObservabilityRepository,
    ObservabilityRepository,
)
from awesome_agent.persistence.budget import (
    ContextCompactionRecord,
    InMemoryBudgetRepository,
    RunBudgetLedgerRecord,
)
from awesome_agent.persistence.team import InMemoryTeamRepository
from awesome_agent.persistence.tool_invocations import (
    DurableToolInvocation,
    InMemoryToolInvocationRepository,
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
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamChildResult,
)
from awesome_agent.runtime.team_mailbox import (
    MailboxMessage,
    MailboxMessageType,
    MailboxRoute,
)
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
    observability_facade: object | None = None,
    budget_repository: InMemoryBudgetRepository | None = None,
    tool_invocation_repository: InMemoryToolInvocationRepository | None = None,
    extension_catalog: ExtensionCatalog | None = None,
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
    team_repository = InMemoryTeamRepository()
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
        extension_catalog_version=(
            extension_catalog.version if extension_catalog is not None else None
        ),
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
                observability_facade=observability_facade,  # type: ignore[arg-type]
                budget_repository=budget_repository,
                tool_invocation_repository=tool_invocation_repository,
                team_repository=team_repository,
                workspace_service=workspace_service,
                extension_catalog=extension_catalog,
            )
        ),
        repository,
    )


class RecordingExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


class FailingFacade:
    async def record_span(self, *_: object, **__: object) -> object:
        raise RuntimeError("observability unavailable")


def _facade(
    repository: InMemoryObservabilityRepository,
    exporter: RecordingExporter,
) -> ObservabilityFacade:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return ObservabilityFacade(
        repository=repository,
        tracer=provider.get_tracer("test"),
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


def test_create_run_pins_extension_catalog_version(tmp_path: Path) -> None:
    catalog = ExtensionCatalog(version="ext_123")
    client, repository = _client(tmp_path, extension_catalog=catalog)

    created = client.post(
        "/runs",
        json={
            "repository_id": str(repository.id),
            "goal": "Inspect extension pin",
            "intent": "read_only",
        },
    )

    assert created.status_code == 201
    run = client.get(f"/runs/{created.json()['id']}").json()
    assert run["extension_catalog_version"] == "ext_123"


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
    assert body["runtime_route"] == "runtime-probe"
    assert "graph_version" not in body


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
    assert any(span["name"] == "run.execute" for span in trace.json())
    assert metrics.status_code == 200
    assert metrics.json()[0]["name"] == "run.duration_ms"
    assert model_calls.status_code == 200
    assert model_calls.json()[0]["model"] == "deepseek-v4-flash"


def test_runtime_diagnostics_summarizes_run_evidence_and_redacts(
    tmp_path: Path,
) -> None:
    observability = InMemoryObservabilityRepository()
    budget_repository = InMemoryBudgetRepository()
    validation_repository = InMemoryValidationRepository()
    tool_invocations = InMemoryToolInvocationRepository()
    client, repository = _client(
        tmp_path,
        observability_repository=observability,
        budget_repository=budget_repository,
        validation_repository=validation_repository,
        tool_invocation_repository=tool_invocations,
    )
    created = client.post(
        "/runs",
        json={
            "repository_id": str(repository.id),
            "goal": "Diagnose this run",
            "intent": "read_only",
        },
    )
    run_id = UUID(created.json()["id"])
    app = cast(Any, client.app)
    runtime_repository = app.state.runtime.repository
    run = asyncio.run(runtime_repository.get_run(run_id))
    asyncio.run(
        runtime_repository.update_run(
            run.model_copy(update={"status": RunStatus.COMPLETED})
        )
    )
    agent = asyncio.run(runtime_repository.list_agents(run_id))[0]
    asyncio.run(
        observability.record_span(
            DurableSpan(
                run_id=run_id,
                trace_id=run_id.hex,
                span_id="span-1",
                parent_span_id=None,
                name="run.execute",
                category="run",
                status="completed",
                attributes={"prompt": "secret prompt", "runtime_route": "solo"},
            )
        )
    )
    asyncio.run(
        observability.record_metric(
            DurableMetric(
                run_id=run_id,
                name="run.duration_ms",
                value=25,
                unit="ms",
                attributes={"status": "completed"},
            )
        )
    )
    asyncio.run(
        observability.record_model_call(
            DurableModelCall(
                run_id=run_id,
                agent_id=agent.id,
                turn=1,
                provider="deepseek",
                model="deepseek-v4-flash",
                status="failed",
                input_tokens=10,
                output_tokens=20,
                reasoning_tokens=3,
                error="secret provider failure",
            )
        )
    )
    asyncio.run(
        budget_repository.upsert_ledger(
            RunBudgetLedgerRecord(
                run_id=run_id,
                total_input_tokens=10,
                total_output_tokens=20,
                total_reasoning_tokens=3,
                active_seconds=7,
                model_call_count=1,
                threshold_status="compact",
            )
        )
    )
    report = DurableValidationReport(
        run_id=run_id,
        agent_id=agent.id,
        attempt=1,
        status="failed",
        summary="validation failed without secrets",
    )
    asyncio.run(
        validation_repository.record_report(
            report,
            gates=[
                DurableValidationGateResult(
                    report_id=report.id,
                    run_id=run_id,
                    gate_id="unit",
                    name="unit tests",
                    command=["pytest"],
                    required=True,
                    status="failed",
                    stdout_summary="secret stdout",
                    stderr_summary="secret stderr",
                    failure_kind="test_failure",
                )
            ],
        )
    )
    asyncio.run(
        tool_invocations.upsert(
            DurableToolInvocation(
                id=uuid4(),
                run_id=run_id,
                agent_id=agent.id,
                tool_name="shell",
                tool_version="1",
                status="failed",
                idempotency_key="tool-1",
                arguments_hash="args-hash",
                risk_level="medium",
                path_refs=["README.md"],
                result_summary="command failed safely",
                result_content="secret tool output",
                result_is_error=True,
                error="secret tool error",
            )
        )
    )

    response = client.get(f"/runs/{run_id}/diagnostics")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == str(run_id)
    assert body["related"]["recovery_metrics"] == f"/runs/{run_id}/recovery-metrics"
    assert body["status"]["status"] == "completed"
    assert body["dispatch"]["status"] == "queued"
    assert body["agents"]["total"] == 1
    assert body["budgets"]["total_tokens"] == 30
    assert body["models"]["total"] == 1
    assert body["models"]["failed"] == 1
    assert body["models"]["calls"][0]["error_present"] is True
    assert body["tools"]["total"] == 1
    assert body["tools"]["tools"][0]["result_summary"] == "command failed safely"
    assert body["validation"]["reports_total"] == 1
    assert body["validation"]["failed_gates"] == 1
    assert body["observability"]["spans_total"] >= 1
    assert body["observability"]["metrics_total"] == 1
    serialized = response.text.lower()
    assert "secret" not in serialized
    assert "cost" not in serialized
    assert "price" not in serialized
    assert "currency" not in serialized


def test_runtime_diagnostics_rolls_up_team_children(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    app = cast(Any, client.app)
    runtime_repository = app.state.runtime.repository
    teams = app.state.team_repository
    root = Run(goal="team", mode=RunMode.TEAM)
    child = Run(
        goal="child",
        mode=RunMode.TEAM,
        parent_run_id=root.id,
        root_run_id=root.id,
        depth=1,
        child_role="teammate",
        status=RunStatus.FAILED,
    )
    leader = Agent(
        run_id=root.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    assignment = TeamAssignment(
        root_run_id=root.id,
        parent_run_id=root.id,
        child_run_id=child.id,
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="teammate",
        runtime_route="team-role",
        goal="child",
        allowed_tools=["read_file"],
    )
    asyncio.run(runtime_repository.create_run(root, leader))
    asyncio.run(
        runtime_repository.create_run(
            child,
            leader.model_copy(update={"run_id": child.id}),
        )
    )
    asyncio.run(teams.create_assignment(assignment))
    asyncio.run(
        teams.record_child_result(
            TeamChildResult(
                assignment_id=assignment.id,
                child_run_id=child.id,
                parent_run_id=root.id,
                root_run_id=root.id,
                status="failed",
                summary="child failed",
                failure_kind="validation_failed",
            )
        )
    )

    body = client.get(f"/runs/{root.id}/diagnostics").json()

    assert body["team"]["assignments_total"] == 1
    assert body["team"]["child_runs_total"] == 1
    assert body["team"]["child_runs"][0]["status"] == "failed"
    assert body["team"]["child_results"][0]["failure_kind"] == "validation_failed"


def test_runtime_diagnostics_returns_404_for_missing_run(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.get("/runs/00000000-0000-0000-0000-000000000000/diagnostics")

    assert response.status_code == 404


def test_extension_catalog_endpoint_reports_redacted_inventory(tmp_path: Path) -> None:
    catalog = ExtensionCatalog(
        version="ext_123",
        sources=[
            ExtensionSourceSnapshot(
                id="local-demo",
                type="static",
                trust="project",
                health=ExtensionHealthSnapshot(status="healthy"),
            )
        ],
        tools=[
            ExtensionToolInventoryItem(
                name="extension.local-demo.demo.search",
                source_id="local-demo",
                description="Search demo content.",
                risk_level="low",
                required_capabilities={"repository:read"},
                input_schema={"type": "object"},
            )
        ],
    )
    client, _ = _client(tmp_path, extension_catalog=catalog)

    response = client.get("/extensions/catalog")

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "ext_123"
    assert body["sources"][0]["id"] == "local-demo"
    assert body["tools"][0]["name"] == "extension.local-demo.demo.search"
    assert "secret" not in response.text.lower()


def test_recovery_metrics_aggregate_team_and_provider_evidence(
    tmp_path: Path,
) -> None:
    observability = InMemoryObservabilityRepository()
    budget_repository = InMemoryBudgetRepository()
    validation_repository = InMemoryValidationRepository()
    client, repository = _client(
        tmp_path,
        observability_repository=observability,
        budget_repository=budget_repository,
        validation_repository=validation_repository,
    )
    created = client.post(
        "/runs",
        json={
            "repository_id": str(repository.id),
            "goal": "Recover this team run",
            "mode": "team",
        },
    )
    run_id = UUID(created.json()["id"])
    app = cast(Any, client.app)
    runtime_repository = app.state.runtime.repository
    teams = app.state.team_repository
    leader = asyncio.run(runtime_repository.list_agents(run_id))[0]
    assignment = TeamAssignment(
        root_run_id=run_id,
        parent_run_id=run_id,
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="teammate",
        runtime_route="team-role",
        goal="repair",
    )
    asyncio.run(teams.create_assignment(assignment))
    asyncio.run(
        teams.record_child_result(
            TeamChildResult(
                assignment_id=assignment.id,
                child_run_id=assignment.child_run_id,
                parent_run_id=run_id,
                root_run_id=run_id,
                status="failed",
                summary="validation failed",
                failure_kind="validation_failed",
            )
        )
    )
    asyncio.run(
        runtime_repository.append_event(
            run_id=run_id,
            event_type=EventType.TEAM_REWORK_REQUESTED,
            payload={"failure_kind": "validation_failed"},
        )
    )
    asyncio.run(
        runtime_repository.append_event(
            run_id=run_id,
            event_type=EventType.BUDGET_EXHAUSTED,
            payload={},
        )
    )
    asyncio.run(
        observability.record_model_call(
            DurableModelCall(
                run_id=run_id,
                agent_id=leader.id,
                turn=1,
                provider="deepseek",
                model="deepseek-v4-flash",
                status="failed",
            )
        )
    )
    asyncio.run(
        budget_repository.upsert_ledger(
            RunBudgetLedgerRecord(
                run_id=run_id,
                total_input_tokens=100,
                total_output_tokens=50,
                total_reasoning_tokens=10,
                active_seconds=12,
                model_call_count=1,
                threshold_status="exhausted",
            )
        )
    )
    report = DurableValidationReport(
        run_id=run_id,
        agent_id=leader.id,
        attempt=1,
        status="failed",
        summary="failed",
    )
    asyncio.run(
        validation_repository.record_report(
            report,
            gates=[
                DurableValidationGateResult(
                    report_id=report.id,
                    run_id=run_id,
                    gate_id="pytest",
                    name="pytest",
                    command=["pytest"],
                    required=True,
                    status="failed",
                    failure_kind="validation_failed",
                )
            ],
        )
    )

    response = client.get(f"/runs/{run_id}/recovery-metrics")

    assert response.status_code == 200
    body = response.json()
    actions = {item["key"]: item["count"] for item in body["by_action"]}
    failures = {item["key"]: item["count"] for item in body["by_failure_kind"]}
    assert actions["verifier_rework"] == 1
    assert actions["same_child_validation_rework"] >= 1
    assert actions["budget_exhausted"] == 1
    assert failures["validation_failed"] >= 1
    assert body["by_role"][0]["key"] == "teammate"
    assert body["by_provider_model"][0]["failed"] == 1
    assert body["budgets"]["total_tokens"] == 150
    assert body["verifier"]["failed_validation_reports"] == 1
    assert body["recommendations"]
    assert "route_attempt_evidence_missing" in {
        warning["kind"] for warning in body["warnings"]
    }
    serialized = response.text.lower()
    assert "cost" not in serialized
    assert "price" not in serialized
    assert "currency" not in serialized


def test_recovery_metrics_returns_404_for_missing_run(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.get(
        "/runs/00000000-0000-0000-0000-000000000000/recovery-metrics"
    )

    assert response.status_code == 404


def test_api_records_manual_endpoint_spans(tmp_path: Path) -> None:
    observability = InMemoryObservabilityRepository()
    exporter = RecordingExporter()
    client, repository = _client(
        tmp_path,
        observability_repository=observability,
        observability_facade=_facade(observability, exporter),
    )

    assert client.get("/health").status_code == 200
    assert client.get("/ready?profile=api").status_code in {200, 503}
    created = client.post(
        "/runs",
        json={
            "repository_id": str(repository.id),
            "goal": "Inspect project",
            "intent": "read_only",
        },
    )
    run_id = UUID(created.json()["id"])
    assert client.get(f"/runs/{run_id}/trace").status_code == 200
    assert client.get(f"/runs/{run_id}/metrics").status_code == 200
    assert client.get(f"/runs/{run_id}/model-calls").status_code == 200

    assert {span.name for span in exporter.spans} >= {
        "api.health",
        "api.ready",
        "api.runs.create",
        "api.runs.trace",
        "api.runs.metrics",
        "api.runs.model_calls",
    }
    assert all("prompt" not in (span.attributes or {}) for span in exporter.spans)


def test_api_observability_failure_preserves_http_status(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, observability_facade=FailingFacade())

    response = client.get("/runs/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


def test_budget_endpoints_return_ledger_and_context_compactions(
    tmp_path: Path,
) -> None:
    budget_repository = InMemoryBudgetRepository()
    client, repository = _client(tmp_path, budget_repository=budget_repository)
    created = client.post(
        "/runs",
        json={
            "repository_id": str(repository.id),
            "goal": "Inspect project",
            "intent": "read_only",
        },
    )
    run_id = UUID(created.json()["id"])
    artifact_id = uuid4()

    async def record() -> None:
        await budget_repository.upsert_ledger(
            RunBudgetLedgerRecord(
                run_id=run_id,
                total_input_tokens=10,
                total_output_tokens=20,
                total_reasoning_tokens=5,
                active_seconds=30,
                model_call_count=2,
                threshold_status="compact",
            )
        )
        await budget_repository.record_compaction(
            ContextCompactionRecord(
                run_id=run_id,
                agent_id=None,
                runtime_route="solo-readonly",
                before_estimated_tokens=50_000,
                after_estimated_tokens=12_000,
                summary="Compacted repository inspection evidence.",
                artifact_refs=[artifact_id],
            )
        )

    asyncio.run(record())

    budget = client.get(f"/runs/{run_id}/budget")
    compactions = client.get(f"/runs/{run_id}/context-compactions")

    assert budget.status_code == 200
    assert budget.json()["total_tokens"] == 30
    assert budget.json()["reasoning_tokens"] == 5
    assert budget.json()["threshold_status"] == "compact"
    assert compactions.status_code == 200
    assert compactions.json()[0]["summary"].startswith("Compacted")
    assert compactions.json()[0]["artifact_refs"] == [str(artifact_id)]


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
    assert body["runtime_route"] == "solo-modifying"
    assert "graph_version" not in body
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
    assert body["runtime_route"] == "team-coding"
    assert "graph_version" not in body
    agents = client.get(f"/runs/{run_id}/agents").json()
    assert len(agents) == 1
    assert agents[0]["kind"] == "leader"
    todos = client.get(f"/runs/{run_id}/todos").json()
    assert todos == []


def test_team_inspection_endpoints_return_lineage_assignments_and_mailbox(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    app = cast(Any, client.app)
    runtime = app.state.runtime.repository
    teams = app.state.team_repository
    root = Run(goal="team", mode=RunMode.TEAM)
    child = Run(
        goal="child",
        mode=RunMode.TEAM,
        parent_run_id=root.id,
        root_run_id=root.id,
        depth=1,
        child_role="teammate",
    )
    leader = Agent(
        run_id=root.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    asyncio.run(runtime.create_run(root, leader))
    asyncio.run(
        runtime.create_run(
            child,
            leader.model_copy(update={"run_id": child.id}),
        )
    )
    assignment = asyncio.run(
        teams.create_assignment(
            TeamAssignment(
                root_run_id=root.id,
                parent_run_id=root.id,
                child_run_id=child.id,
                kind=TeamAssignmentKind.TEAMMATE,
                role_profile="teammate",
                runtime_route="team-role",
                goal="child",
                allowed_tools=["repo.read", "repo.apply_patch"],
            )
        )
    )
    asyncio.run(
        teams.create_mailbox_message(
            MailboxMessage(
                team_root_run_id=root.id,
                sender_run_id=root.id,
                recipient_run_id=child.id,
                route=MailboxRoute.LEADER_TO_TEAMMATE,
                message_type=MailboxMessageType.ASSIGNMENT,
                subject="Task",
                body_summary="Do it.",
            )
        )
    )

    children = client.get(f"/runs/{root.id}/children").json()
    descendants = client.get(f"/runs/{root.id}/descendants").json()
    assignments = client.get(f"/runs/{root.id}/team/assignments").json()
    mailbox = client.get(f"/runs/{child.id}/team/mailbox").json()
    retired = client.post(
        f"/runs/{root.id}/team/assignments/{assignment.id}/retire",
        params={"reason": "superseded"},
    )
    active_after_retire = client.get(f"/runs/{root.id}/team/assignments").json()
    all_after_retire = client.get(
        f"/runs/{root.id}/team/assignments",
        params={"all": "true"},
    ).json()

    assert children[0]["id"] == str(child.id)
    assert descendants[0]["id"] == str(child.id)
    assert assignments[0]["id"] == str(assignment.id)
    assert assignments[0]["allowed_tools"] == ["repo.read", "repo.apply_patch"]
    assert assignments[0]["effective_tools"] == ["repo.read"]
    assert "repository:read" in assignments[0]["effective_capabilities"]
    assert {item["tool"]: item["reason"] for item in assignments[0]["denied_tools"]}[
        "repo.apply_patch"
    ] == "requires_write"
    assert mailbox[0]["route"] == "leader_to_teammate"
    assert retired.json()["status"] == "retired"
    assert active_after_retire == []
    assert all_after_retire[0]["status"] == "retired"


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
