from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, cast
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, StreamingResponse

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.api.schemas import (
    ApprovalDecisionRequest,
    BudgetLedgerResponse,
    ContextCompactionResponse,
    CreateProbeRequest,
    CreateRunRequest,
    DispatchResponse,
    HealthCheckResponse,
    ReadinessReportResponse,
    WorkspaceCandidateResponse,
    WorkspaceCleanupRequest,
)
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import ExecutionKind, RunIntent
from awesome_agent.domain.models import RuntimeEvent
from awesome_agent.health import (
    HealthCheck,
    HealthStatus,
    ReadinessProfile,
    ReadinessReport,
    bind_policy_check,
    collect_readiness,
)
from awesome_agent.observability.repository import (
    NoopObservabilityRepository,
    ObservabilityRepository,
    PostgresObservabilityRepository,
)
from awesome_agent.persistence.artifacts import PostgresArtifactMetadataRepository
from awesome_agent.persistence.budget import BudgetRepository, PostgresBudgetRepository
from awesome_agent.persistence.database import (
    create_engine,
    create_session_factory,
)
from awesome_agent.persistence.dispatch import PostgresRunDispatcher
from awesome_agent.persistence.intake_reservations import (
    PostgresIntakeReservationStore,
)
from awesome_agent.persistence.repository_registry import (
    PostgresRepositoryRegistry,
)
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.persistence.validation import (
    PostgresValidationRepository,
    ValidationReportWithGates,
    ValidationRepository,
)
from awesome_agent.persistence.worker_heartbeats import (
    PostgresWorkerHeartbeatRepository,
)
from awesome_agent.repositories.config import LocalRepositoryConfigStore
from awesome_agent.repositories.registry import RepositoryRegistry
from awesome_agent.repositories.worktrees import ManagedRunWorktreeManager
from awesome_agent.runtime.dispatch import DispatchConflict
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.intake import RunIntakeError, RunIntakeService
from awesome_agent.runtime.probe_graph import (
    RUNTIME_PROBE_GRAPH,
    RUNTIME_PROBE_VERSION,
)
from awesome_agent.runtime.service import RuntimeService
from awesome_agent.runtime.workspaces import (
    WorkspaceCandidate,
    WorkspaceRetentionService,
    parse_workspace_age,
)
from awesome_agent.runtime.workspaces import (
    WorkspaceCleanupRequest as RuntimeWorkspaceCleanupRequest,
)
from awesome_agent.settings import Settings


def create_app(
    service: RuntimeService | None = None,
    *,
    settings: Settings | None = None,
    intake: RunIntakeService | None = None,
    registry: RepositoryRegistry | None = None,
    validation_repository: ValidationRepository | None = None,
    observability_repository: ObservabilityRepository | None = None,
    budget_repository: BudgetRepository | None = None,
    workspace_service: WorkspaceRetentionService | None = None,
    worker_heartbeat_repository: object | None = None,
) -> FastAPI:
    settings = settings or Settings()
    bind_check = bind_policy_check(settings.api_host, settings.unsafe_bind_public)
    if bind_check.status is HealthStatus.UNHEALTHY:
        raise RuntimeError(bind_check.detail)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is not None and intake is not None and registry is not None:
            app.state.runtime = service
            app.state.intake = intake
            app.state.registry = registry
            if workspace_service is not None:
                app.state.workspaces = workspace_service
            if validation_repository is not None:
                app.state.validation_repository = validation_repository
            app.state.observability_repository = (
                observability_repository or NoopObservabilityRepository()
            )
            if budget_repository is not None:
                app.state.budget_repository = budget_repository
            if worker_heartbeat_repository is not None:
                app.state.worker_heartbeats = worker_heartbeat_repository
            yield
            return

        engine = create_engine(settings.database_url)
        sessions = create_session_factory(engine)
        event_stream = EventStream()
        repository_registry = PostgresRepositoryRegistry(sessions)
        reservations = PostgresIntakeReservationStore(sessions)
        runtime_repository = PostgresRuntimeRepository(sessions)
        dispatcher = PostgresRunDispatcher(sessions)
        validation = PostgresValidationRepository(sessions)
        worker_heartbeats = PostgresWorkerHeartbeatRepository(sessions)
        budgets = PostgresBudgetRepository(sessions)
        local_config = LocalRepositoryConfigStore(settings.local_config_path).load()
        app.state.runtime = RuntimeService(
            repository=runtime_repository,
            events=event_stream,
            artifacts=LocalArtifactStore(settings.artifact_root),
            artifact_repository=PostgresArtifactMetadataRepository(sessions),
            dispatcher=dispatcher,
            model_resolver=RoleModelResolver.from_settings(settings),
            event_poll_interval=settings.event_poll_interval_seconds,
        )
        app.state.registry = repository_registry
        app.state.validation_repository = validation
        app.state.worker_heartbeats = worker_heartbeats
        app.state.observability_repository = observability_repository or (
            PostgresObservabilityRepository(sessions)
        )
        app.state.budget_repository = budget_repository or budgets
        worktree_manager = ManagedRunWorktreeManager(
            settings.workspace_root or local_config.workspace_root
        )
        app.state.intake = RunIntakeService(
            registry=repository_registry,
            reservations=reservations,
            runtime=runtime_repository,
            events=event_stream,
            worktrees=worktree_manager,
            allowed_roots=local_config.allowed_roots,
            model_resolver=RoleModelResolver.from_settings(settings),
        )
        app.state.workspaces = WorkspaceRetentionService(
            runtime_repository=runtime_repository,
            repository_registry=repository_registry,
            worktrees=worktree_manager,
        )
        await app.state.intake.reconcile_incomplete()
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="awesome_agent", version="0.1.0", lifespan=lifespan)
    if service is not None:
        app.state.runtime = service
    if intake is not None:
        app.state.intake = intake
    if registry is not None:
        app.state.registry = registry
    if workspace_service is not None:
        app.state.workspaces = workspace_service
    if validation_repository is not None:
        app.state.validation_repository = validation_repository
    if worker_heartbeat_repository is not None:
        app.state.worker_heartbeats = worker_heartbeat_repository
    app.state.observability_repository = (
        observability_repository or NoopObservabilityRepository()
    )
    if budget_repository is not None:
        app.state.budget_repository = budget_repository

    def runtime() -> RuntimeService:
        return cast(RuntimeService, app.state.runtime)

    def run_intake() -> RunIntakeService:
        return cast(RunIntakeService, app.state.intake)

    def repositories() -> RepositoryRegistry:
        return cast(RepositoryRegistry, app.state.registry)

    def validation_reports() -> ValidationRepository | None:
        return cast(
            ValidationRepository | None,
            getattr(app.state, "validation_repository", None),
        )

    def observability() -> ObservabilityRepository:
        return cast(
            ObservabilityRepository,
            app.state.observability_repository,
        )

    def budgets() -> BudgetRepository | None:
        return cast(
            BudgetRepository | None,
            getattr(app.state, "budget_repository", None),
        )

    def workspaces() -> WorkspaceRetentionService:
        return cast(WorkspaceRetentionService, app.state.workspaces)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(
        response: Response,
        profile: Annotated[ReadinessProfile, Query()] = ReadinessProfile.API,
    ) -> ReadinessReportResponse:
        report = await collect_readiness(
            settings,
            profile,
            worker_heartbeat_repository=getattr(
                app.state,
                "worker_heartbeats",
                None,
            ),
        )
        if report.status is HealthStatus.UNHEALTHY:
            response.status_code = 503
        return _readiness_report_response(report)

    @app.post("/runs", status_code=201)
    async def create_run(request: CreateRunRequest) -> dict[str, object]:
        try:
            run = await run_intake().create_run(
                repository_id=request.repository_id,
                goal=request.goal,
                intent=request.intent,
                mode=request.mode,
            )
        except KeyError as error:
            raise HTTPException(
                status_code=404,
                detail="Repository not found.",
            ) from error
        except (RunIntakeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return run.model_dump(mode="json")

    @app.post("/runtime/probes", status_code=201)
    async def create_probe(request: CreateProbeRequest) -> dict[str, object]:
        try:
            run = await run_intake().create_run(
                repository_id=request.repository_id,
                goal=request.goal,
                intent=RunIntent.READ_ONLY,
                execution_kind=ExecutionKind.RUNTIME_PROBE,
                graph_name=RUNTIME_PROBE_GRAPH,
                graph_version=RUNTIME_PROBE_VERSION,
            )
        except KeyError as error:
            raise HTTPException(
                status_code=404,
                detail="Repository not found.",
            ) from error
        except (RunIntakeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return run.model_dump(mode="json")

    @app.get("/repositories")
    async def list_repositories() -> list[dict[str, object]]:
        return [
            repository.model_dump(mode="json")
            for repository in await repositories().list()
        ]

    @app.get("/repositories/{repository_id}")
    async def get_repository(repository_id: UUID) -> dict[str, object]:
        try:
            repository = await repositories().get(repository_id)
        except KeyError as error:
            raise HTTPException(
                status_code=404,
                detail="Repository not found.",
            ) from error
        return repository.model_dump(mode="json")

    @app.get("/workspaces")
    async def list_workspaces() -> list[WorkspaceCandidateResponse]:
        return [
            _workspace_candidate_response(candidate)
            for candidate in await workspaces().list_candidates()
        ]

    @app.post("/workspaces/cleanup-preview")
    async def cleanup_workspaces_preview(
        request: WorkspaceCleanupRequest,
    ) -> list[WorkspaceCandidateResponse]:
        try:
            candidates = await workspaces().cleanup_preview(
                _workspace_cleanup_request(request, apply=False)
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return [_workspace_candidate_response(candidate) for candidate in candidates]

    @app.post("/workspaces/cleanup")
    async def cleanup_workspaces(
        request: WorkspaceCleanupRequest,
    ) -> list[WorkspaceCandidateResponse]:
        try:
            candidates = await workspaces().cleanup(
                _workspace_cleanup_request(request, apply=True)
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return [_workspace_candidate_response(candidate) for candidate in candidates]

    @app.get("/runs/{run_id}")
    async def get_run(run_id: UUID) -> dict[str, object]:
        try:
            return (await runtime().get_run(run_id)).model_dump(mode="json")
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error

    @app.get("/runs/{run_id}/dispatch")
    async def get_dispatch(run_id: UUID) -> DispatchResponse:
        try:
            run = await runtime().get_run(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error
        return DispatchResponse(
            status=run.dispatch_status.value,
            available_at=run.available_at,
            worker_id=run.current_worker_id,
            worker_name=run.current_worker_name,
            fencing_token=run.fencing_token,
            attempt=run.attempt,
            lease_acquired_at=run.lease_acquired_at,
            lease_expires_at=run.lease_expires_at,
            heartbeat_at=run.heartbeat_at,
            last_release_reason=run.last_release_reason,
            last_error=run.last_dispatch_error,
        )

    @app.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: UUID) -> dict[str, object]:
        try:
            run = await runtime().cancel_run(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error
        except DispatchConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return run.model_dump(mode="json")

    @app.post("/runs/{run_id}/resume")
    async def resume_run(run_id: UUID) -> dict[str, object]:
        try:
            run = await runtime().resume_run(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return run.model_dump(mode="json")

    @app.get("/runs/{run_id}/agents")
    async def list_agents(run_id: UUID) -> list[dict[str, object]]:
        return [
            agent.model_dump(mode="json")
            for agent in await runtime().list_agents(run_id)
        ]

    @app.get("/runs/{run_id}/todos")
    async def list_todos(run_id: UUID) -> list[dict[str, object]]:
        return [
            todo.model_dump(mode="json") for todo in await runtime().list_todos(run_id)
        ]

    @app.get("/runs/{run_id}/events/history")
    async def event_history(
        run_id: UUID,
        after_sequence: int = Query(default=0, ge=0),
    ) -> list[dict[str, object]]:
        return [
            event.model_dump(mode="json")
            for event in await runtime().list_events(
                run_id,
                after_sequence=after_sequence,
            )
        ]

    @app.get("/runs/{run_id}/events")
    async def stream_events(
        run_id: UUID,
        after_sequence: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        return StreamingResponse(
            _sse(runtime(), run_id, after_sequence=after_sequence),
            media_type="text/event-stream",
        )

    @app.get("/runs/{run_id}/messages")
    async def list_messages(run_id: UUID) -> list[dict[str, object]]:
        return [
            event.model_dump(mode="json")
            for event in await runtime().list_events(run_id)
            if event.event_type.value == "message.created"
        ]

    @app.get("/runs/{run_id}/artifacts")
    async def list_artifacts(run_id: UUID) -> list[dict[str, object]]:
        return [
            artifact.model_dump(mode="json")
            for artifact in await runtime().list_artifacts(run_id)
        ]

    @app.get("/artifacts/{artifact_id}")
    async def download_artifact(artifact_id: UUID) -> FileResponse:
        try:
            artifact = await runtime().get_artifact(artifact_id)
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="Artifact not found."
            ) from error
        return FileResponse(
            Path(artifact.path),
            media_type=artifact.mime_type,
            filename=Path(artifact.path).name,
        )

    @app.get("/runs/{run_id}/approvals")
    async def list_approvals(run_id: UUID) -> list[dict[str, object]]:
        return [
            event.model_dump(mode="json")
            for event in await runtime().list_events(run_id)
            if event.event_type.value.startswith("approval.")
        ]

    @app.post("/runs/{run_id}/approvals/{approval_id}")
    async def decide_approval(
        run_id: UUID,
        approval_id: UUID,
        request: ApprovalDecisionRequest,
    ) -> dict[str, object]:
        try:
            event = await runtime().decide_approval(
                run_id,
                approval_id=approval_id,
                approved=request.approved,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error
        return event.model_dump(mode="json")

    @app.get("/runs/{run_id}/verification")
    async def list_verification(run_id: UUID) -> list[dict[str, object]]:
        repository = validation_reports()
        if repository is None:
            return []
        return [
            _verification_report_response(report)
            for report in await repository.list_for_run(run_id)
        ]

    @app.get("/runs/{run_id}/trace")
    async def list_trace(run_id: UUID) -> list[dict[str, object]]:
        return [
            asdict(span) for span in await observability().list_spans_for_run(run_id)
        ]

    @app.get("/runs/{run_id}/metrics")
    async def list_metrics(run_id: UUID) -> list[dict[str, object]]:
        return [
            asdict(metric)
            for metric in await observability().list_metrics_for_run(run_id)
        ]

    @app.get("/runs/{run_id}/model-calls")
    async def list_model_calls(run_id: UUID) -> list[dict[str, object]]:
        return [
            asdict(call)
            for call in await observability().list_model_calls_for_run(run_id)
        ]

    @app.get("/runs/{run_id}/budget")
    async def get_budget(run_id: UUID) -> BudgetLedgerResponse:
        try:
            await runtime().get_run(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error
        repository = budgets()
        if repository is None:
            return BudgetLedgerResponse(
                run_id=run_id,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                reasoning_tokens=0,
                active_seconds=0,
                model_call_count=0,
                threshold_status="within_budget",
            )
        ledger = await repository.get_ledger(run_id)
        return BudgetLedgerResponse(
            run_id=run_id,
            input_tokens=ledger.total_input_tokens,
            output_tokens=ledger.total_output_tokens,
            total_tokens=ledger.total_input_tokens + ledger.total_output_tokens,
            reasoning_tokens=ledger.total_reasoning_tokens,
            active_seconds=ledger.active_seconds,
            model_call_count=ledger.model_call_count,
            threshold_status=ledger.threshold_status,
        )

    @app.get("/runs/{run_id}/context-compactions")
    async def list_context_compactions(
        run_id: UUID,
    ) -> list[ContextCompactionResponse]:
        try:
            await runtime().get_run(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error
        repository = budgets()
        if repository is None:
            return []
        return [
            ContextCompactionResponse(
                id=compaction.id,
                run_id=compaction.run_id,
                agent_id=compaction.agent_id,
                graph_name=compaction.graph_name,
                graph_version=compaction.graph_version,
                before_estimated_tokens=compaction.before_estimated_tokens,
                after_estimated_tokens=compaction.after_estimated_tokens,
                summary=compaction.summary,
                artifact_refs=compaction.artifact_refs,
                created_at=compaction.created_at,
            )
            for compaction in await repository.list_compactions(run_id)
        ]

    return app


async def _sse(
    runtime: RuntimeService,
    run_id: UUID,
    *,
    after_sequence: int,
) -> AsyncIterator[str]:
    async for event in runtime.stream_events(
        run_id,
        after_sequence=after_sequence,
    ):
        yield _format_sse(event)


def _format_sse(event: RuntimeEvent) -> str:
    data = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
    return f"id: {event.sequence}\nevent: {event.event_type.value}\ndata: {data}\n\n"


def _readiness_report_response(report: ReadinessReport) -> ReadinessReportResponse:
    return ReadinessReportResponse(
        profile=report.profile.value,
        status=report.status.value,
        generated_at=report.generated_at,
        checks=[_health_check_response(check) for check in report.checks],
    )


def _health_check_response(check: HealthCheck) -> HealthCheckResponse:
    return HealthCheckResponse(
        name=check.name,
        status=check.status.value,
        severity=check.severity.value,
        detail=check.detail,
        remediation=check.remediation,
        metadata=check.metadata,
    )


def _verification_report_response(
    item: ValidationReportWithGates,
) -> dict[str, object]:
    return {
        "id": str(item.report.id),
        "run_id": str(item.report.run_id),
        "agent_id": str(item.report.agent_id) if item.report.agent_id else None,
        "attempt": item.report.attempt,
        "status": item.report.status,
        "summary": item.report.summary,
        "created_at": item.report.created_at.isoformat(),
        "gates": [
            {
                "id": str(gate.id),
                "report_id": str(gate.report_id),
                "run_id": str(gate.run_id),
                "gate_id": gate.gate_id,
                "name": gate.name,
                "command": gate.command,
                "required": gate.required,
                "status": gate.status,
                "exit_code": gate.exit_code,
                "duration_ms": gate.duration_ms,
                "stdout_summary": gate.stdout_summary,
                "stderr_summary": gate.stderr_summary,
                "artifact_refs": gate.artifact_refs,
                "failure_kind": gate.failure_kind,
                "created_at": gate.created_at.isoformat(),
            }
            for gate in item.gates
        ],
    }


def _workspace_cleanup_request(
    request: WorkspaceCleanupRequest,
    *,
    apply: bool,
) -> RuntimeWorkspaceCleanupRequest:
    return RuntimeWorkspaceCleanupRequest(
        run_id=request.run_id,
        older_than=parse_workspace_age(request.older_than),
        apply=apply,
        force=request.force,
        reason=request.reason,
    )


def _workspace_candidate_response(
    candidate: WorkspaceCandidate,
) -> WorkspaceCandidateResponse:
    return WorkspaceCandidateResponse(
        run_id=candidate.run_id,
        repository_id=candidate.repository_id,
        workspace_path=(
            str(candidate.workspace_path)
            if candidate.workspace_path is not None
            else None
        ),
        branch=candidate.branch,
        status=candidate.status.value,
        retention_status=candidate.retention_status.value,
        reason=candidate.reason,
        dirty=candidate.dirty,
        can_cleanup=candidate.can_cleanup,
    )


app = create_app()
