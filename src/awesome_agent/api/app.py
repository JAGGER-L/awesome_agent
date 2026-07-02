from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Annotated, cast
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, StreamingResponse

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.api.schemas import (
    ApprovalDecisionRequest,
    BudgetLedgerResponse,
    ContextCompactionResponse,
    CreateConversationTurnRequest,
    CreateProbeRequest,
    CreateRunRequest,
    CreateThreadMessageRequest,
    CreateThreadRequest,
    CreateThreadRunRequest,
    DispatchResponse,
    HealthCheckResponse,
    ReadinessReportResponse,
    WorkspaceCandidateResponse,
    WorkspaceCleanupRequest,
)
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.conversation.events import ConversationStreamEvent
from awesome_agent.conversation.repository import ConversationRepository
from awesome_agent.conversation.service import (
    ConversationService,
    MissingThreadRepositoryContext,
)
from awesome_agent.domain.enums import ExecutionKind, RunIntent
from awesome_agent.domain.models import RuntimeEvent
from awesome_agent.extensions.config import build_project_extension_catalog_sync
from awesome_agent.extensions.diagnostics import (
    ExtensionDiagnosticsService,
    diff_extension_catalogs,
)
from awesome_agent.extensions.models import ExtensionCatalog
from awesome_agent.health import (
    HealthCheck,
    HealthStatus,
    ReadinessProfile,
    ReadinessReport,
    bind_policy_check,
    collect_readiness,
)
from awesome_agent.observability.facade import (
    ObservabilityFacade,
    ObservabilitySpanInput,
)
from awesome_agent.observability.otel import (
    OTelConfig,
    configure_otel,
    configure_otel_metrics,
)
from awesome_agent.observability.repository import (
    NoopObservabilityRepository,
    ObservabilityRepository,
    PostgresObservabilityRepository,
)
from awesome_agent.persistence.approvals import PostgresApprovalRepository
from awesome_agent.persistence.artifacts import PostgresArtifactMetadataRepository
from awesome_agent.persistence.budget import BudgetRepository, PostgresBudgetRepository
from awesome_agent.persistence.conversations import (
    InMemoryConversationRepository,
    PostgresConversationRepository,
)
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
from awesome_agent.persistence.team import PostgresTeamRepository, TeamRepository
from awesome_agent.persistence.tool_invocations import (
    PostgresToolInvocationRepository,
    ToolInvocationRepository,
)
from awesome_agent.persistence.validation import (
    PostgresValidationRepository,
    ValidationReportWithGates,
    ValidationRepository,
)
from awesome_agent.persistence.worker_heartbeats import (
    PostgresWorkerHeartbeatRepository,
)
from awesome_agent.providers.factory import ModelProviderFactory
from awesome_agent.repositories.config import LocalRepositoryConfigStore
from awesome_agent.repositories.registry import RepositoryRegistry
from awesome_agent.repositories.service import RepositoryService
from awesome_agent.repositories.worktrees import ManagedRunWorktreeManager
from awesome_agent.runtime.asyncio import configure_event_loop_policy
from awesome_agent.runtime.capabilities import CapabilityPurpose, CapabilityResolver
from awesome_agent.runtime.diagnostics import RunDiagnosticsService
from awesome_agent.runtime.dispatch import DispatchConflict
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.intake import RunIntakeError, RunIntakeService
from awesome_agent.runtime.probe_graph import RUNTIME_PROBE_ROUTE
from awesome_agent.runtime.recovery_metrics import RecoveryMetricsService
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

logger = logging.getLogger(__name__)
_NIL_RUN_ID = UUID(int=0)

configure_event_loop_policy()


def create_app(
    service: RuntimeService | None = None,
    *,
    settings: Settings | None = None,
    intake: RunIntakeService | None = None,
    registry: RepositoryRegistry | None = None,
    validation_repository: ValidationRepository | None = None,
    observability_repository: ObservabilityRepository | None = None,
    observability_facade: ObservabilityFacade | None = None,
    budget_repository: BudgetRepository | None = None,
    tool_invocation_repository: ToolInvocationRepository | None = None,
    team_repository: TeamRepository | None = None,
    workspace_service: WorkspaceRetentionService | None = None,
    worker_heartbeat_repository: object | None = None,
    extension_catalog: ExtensionCatalog | None = None,
    extension_catalog_history: list[ExtensionCatalog] | None = None,
    project_root: Path | None = None,
    thread_repository: ConversationRepository | None = None,
    conversation_service: ConversationService | None = None,
) -> FastAPI:
    settings = settings or Settings()
    threads_repository = thread_repository or InMemoryConversationRepository()
    model_provider_factory = ModelProviderFactory(settings)
    default_conversation_service = conversation_service or ConversationService(
        repository=threads_repository,
        provider_factory=model_provider_factory.create,
        default_model=settings.leader_model,
    )
    active_extension_catalog = extension_catalog
    if active_extension_catalog is None:
        active_extension_catalog = build_project_extension_catalog_sync(project_root)
    extension_catalogs_by_version = {
        catalog.version: catalog
        for catalog in [*(extension_catalog_history or []), active_extension_catalog]
    }
    bind_check = bind_policy_check(settings.api_host, settings.unsafe_bind_public)
    if bind_check.status is HealthStatus.UNHEALTHY:
        raise RuntimeError(bind_check.detail)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is not None and intake is not None and registry is not None:
            app.state.runtime = service
            app.state.intake = intake
            app.state.registry = registry
            app.state.extension_catalog = active_extension_catalog
            app.state.threads = threads_repository
            app.state.conversations = default_conversation_service
            app.state.extension_catalogs_by_version = extension_catalogs_by_version
            if workspace_service is not None:
                app.state.workspaces = workspace_service
            if validation_repository is not None:
                app.state.validation_repository = validation_repository
            configured_observability = (
                observability_repository or NoopObservabilityRepository()
            )
            app.state.observability_repository = configured_observability
            app.state.observability_facade = (
                observability_facade
                or ObservabilityFacade(repository=configured_observability)
            )
            if budget_repository is not None:
                app.state.budget_repository = budget_repository
            if tool_invocation_repository is not None:
                app.state.tool_invocation_repository = tool_invocation_repository
            if team_repository is not None:
                app.state.team_repository = team_repository
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
        tool_invocations = PostgresToolInvocationRepository(sessions)
        worker_heartbeats = PostgresWorkerHeartbeatRepository(sessions)
        default_observability = observability_repository or (
            PostgresObservabilityRepository(sessions)
        )
        otel_config = OTelConfig(
            service_name=settings.otel_service_name,
            process_kind="api",
            console_exporter=settings.otel_console_exporter_enabled,
            otlp_endpoint=settings.otel_otlp_endpoint,
        )
        otel_provider = (
            configure_otel(otel_config) if settings.observability_enabled else None
        )
        otel_metrics = (
            configure_otel_metrics(otel_config)
            if settings.observability_enabled
            else None
        )
        budgets = PostgresBudgetRepository(sessions)
        teams = PostgresTeamRepository(sessions)
        local_config = LocalRepositoryConfigStore(settings.local_config_path).load()
        app.state.runtime = RuntimeService(
            repository=runtime_repository,
            events=event_stream,
            artifacts=LocalArtifactStore(settings.artifact_root),
            artifact_repository=PostgresArtifactMetadataRepository(sessions),
            approval_repository=PostgresApprovalRepository(sessions),
            dispatcher=dispatcher,
            model_resolver=RoleModelResolver.from_settings(settings),
            event_poll_interval=settings.event_poll_interval_seconds,
        )
        app.state.extension_catalog = active_extension_catalog
        app.state.threads = PostgresConversationRepository(sessions)
        app.state.conversations = conversation_service or ConversationService(
            repository=app.state.threads,
            provider_factory=ModelProviderFactory(settings).create,
            default_model=settings.leader_model,
        )
        app.state.extension_catalogs_by_version = extension_catalogs_by_version
        app.state.registry = repository_registry
        app.state.validation_repository = validation
        app.state.worker_heartbeats = worker_heartbeats
        app.state.observability_repository = default_observability
        app.state.observability_facade = observability_facade or ObservabilityFacade(
            repository=default_observability,
            tracer=(
                otel_provider.get_tracer("awesome_agent.api")
                if otel_provider is not None
                else None
            ),
            metric_recorder=otel_metrics,
        )
        app.state.budget_repository = budget_repository or budgets
        app.state.tool_invocation_repository = (
            tool_invocation_repository or tool_invocations
        )
        app.state.team_repository = team_repository or teams
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
            extension_catalog_version=active_extension_catalog.version,
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
    app.state.extension_catalog = active_extension_catalog
    app.state.threads = threads_repository
    app.state.conversations = default_conversation_service
    app.state.extension_catalogs_by_version = extension_catalogs_by_version
    if workspace_service is not None:
        app.state.workspaces = workspace_service
    if validation_repository is not None:
        app.state.validation_repository = validation_repository
    if worker_heartbeat_repository is not None:
        app.state.worker_heartbeats = worker_heartbeat_repository
    initial_observability = observability_repository or NoopObservabilityRepository()
    app.state.observability_repository = initial_observability
    app.state.observability_facade = observability_facade or ObservabilityFacade(
        repository=initial_observability,
    )
    if budget_repository is not None:
        app.state.budget_repository = budget_repository
    if tool_invocation_repository is not None:
        app.state.tool_invocation_repository = tool_invocation_repository
    if team_repository is not None:
        app.state.team_repository = team_repository

    def runtime() -> RuntimeService:
        return cast(RuntimeService, app.state.runtime)

    def run_intake() -> RunIntakeService:
        return cast(RunIntakeService, app.state.intake)

    def repositories() -> RepositoryRegistry:
        return cast(RepositoryRegistry, app.state.registry)

    def team_repository_state() -> TeamRepository:
        return cast(TeamRepository, app.state.team_repository)

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

    def tool_invocations() -> ToolInvocationRepository | None:
        return cast(
            ToolInvocationRepository | None,
            getattr(app.state, "tool_invocation_repository", None),
        )

    def telemetry() -> ObservabilityFacade:
        return cast(ObservabilityFacade, app.state.observability_facade)

    @asynccontextmanager
    async def api_span(
        name: str,
        *,
        run_id: UUID | Callable[[], UUID] = _NIL_RUN_ID,
        attributes: dict[str, object] | None = None,
    ) -> AsyncIterator[None]:
        started_at = datetime.now(UTC)
        started = monotonic()
        status = "completed"
        error_text: str | None = None
        try:
            yield
        except Exception as error:
            status = "failed"
            error_text = str(error)
            raise
        finally:
            duration_ms = max(0, int((monotonic() - started) * 1000))
            try:
                await telemetry().record_span(
                    ObservabilitySpanInput(
                        run_id=run_id() if callable(run_id) else run_id,
                        name=name,
                        category="api",
                        status=status,
                        attributes=attributes or {},
                        started_at=started_at,
                        ended_at=datetime.now(UTC),
                        duration_ms=duration_ms,
                        error=error_text,
                    )
                )
            except Exception:
                logger.exception("API observability span recording failed.")

    def budgets() -> BudgetRepository | None:
        return cast(
            BudgetRepository | None,
            getattr(app.state, "budget_repository", None),
        )

    def workspaces() -> WorkspaceRetentionService:
        return cast(WorkspaceRetentionService, app.state.workspaces)

    def extensions_catalog() -> ExtensionCatalog:
        return cast(ExtensionCatalog, app.state.extension_catalog)

    def extension_catalog_history_state() -> dict[str, ExtensionCatalog]:
        return cast(
            dict[str, ExtensionCatalog],
            app.state.extension_catalogs_by_version,
        )

    def threads() -> ConversationRepository:
        return cast(ConversationRepository, app.state.threads)

    def conversations() -> ConversationService:
        return cast(ConversationService, app.state.conversations)

    @app.get("/health")
    async def health() -> dict[str, str]:
        async with api_span(
            "api.health",
            attributes=_api_attributes("GET", "/health", 200),
        ):
            return {"status": "ok"}

    @app.get("/extensions/catalog")
    async def get_extensions_catalog() -> dict[str, object]:
        return cast(dict[str, object], extensions_catalog().model_dump(mode="json"))

    @app.get("/extensions/diagnostics")
    async def get_extensions_diagnostics() -> dict[str, object]:
        diagnostics = ExtensionDiagnosticsService(
            active_catalog=extensions_catalog(),
            runtime_repository=runtime().repository,
            tool_invocation_repository=tool_invocations(),
        )
        return cast(
            dict[str, object],
            (await diagnostics.summarize()).model_dump(mode="json"),
        )

    @app.get("/extensions/catalog-diff")
    async def get_extensions_catalog_diff(
        from_version: str,
        to_version: str,
    ) -> dict[str, object]:
        catalogs = extension_catalog_history_state()
        try:
            before = catalogs[from_version]
            after = catalogs[to_version]
        except KeyError as error:
            raise HTTPException(
                status_code=404,
                detail="Catalog version not found.",
            ) from error
        return cast(
            dict[str, object],
            diff_extension_catalogs(before, after).model_dump(mode="json"),
        )

    @app.get("/ready")
    async def ready(
        response: Response,
        profile: Annotated[ReadinessProfile, Query()] = ReadinessProfile.API,
    ) -> ReadinessReportResponse:
        attributes = _api_attributes("GET", "/ready", 200)
        async with api_span("api.ready", attributes=attributes):
            report = await collect_readiness(
                settings,
                profile,
                check_docker=settings.readiness_check_docker,
                worker_heartbeat_repository=getattr(
                    app.state,
                    "worker_heartbeats",
                    None,
                ),
            )
            if report.status is HealthStatus.UNHEALTHY:
                response.status_code = 503
                attributes["http.status_code"] = 503
            return _readiness_report_response(report)

    @app.post("/threads")
    async def create_thread(request: CreateThreadRequest) -> dict[str, object]:
        thread = await threads().create_thread(
            title=request.title,
            context_kind=request.context_kind,
            context_path=request.context_path,
            repository_id=request.repository_id,
            default_model=request.default_model,
            sandbox_profile=request.sandbox_profile,
        )
        return thread.api_payload()

    @app.get("/threads")
    async def list_threads() -> list[dict[str, object]]:
        return [thread.api_payload() for thread in await threads().list_threads()]

    @app.get("/threads/resume")
    async def resume_thread(query: str) -> dict[str, object]:
        try:
            thread = await threads().resolve_thread(query)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Thread not found.") from error
        return thread.api_payload()

    @app.get("/threads/{thread_id}")
    async def get_thread(thread_id: UUID) -> dict[str, object]:
        try:
            thread = await threads().get_thread(thread_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Thread not found.") from error
        return thread.api_payload()

    @app.post("/threads/{thread_id}/messages")
    async def append_thread_message(
        thread_id: UUID,
        request: CreateThreadMessageRequest,
    ) -> dict[str, object]:
        try:
            message = await threads().append_message(
                thread_id=thread_id,
                role=request.role,
                content=request.content,
                kind=request.kind,
                run_id=request.run_id,
                metadata=request.metadata,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Thread not found.") from error
        return message.model_dump(mode="json")

    @app.get("/threads/{thread_id}/messages")
    async def list_thread_messages(thread_id: UUID) -> list[dict[str, object]]:
        try:
            messages = await threads().list_messages(thread_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Thread not found.") from error
        return [message.model_dump(mode="json") for message in messages]

    @app.post("/threads/{thread_id}/turns")
    async def create_conversation_turn(
        thread_id: UUID,
        request: CreateConversationTurnRequest,
    ) -> StreamingResponse:
        return StreamingResponse(
            _conversation_sse(
                conversations().start_turn(
                    thread_id=thread_id,
                    content=request.content,
                    model=request.model,
                )
            ),
            media_type="text/event-stream",
        )

    @app.post("/threads/{thread_id}/runs", status_code=201)
    async def create_thread_run(
        thread_id: UUID,
        request: CreateThreadRunRequest,
    ) -> dict[str, object]:
        if request.repository_id is not None and request.repository_path is not None:
            raise HTTPException(
                status_code=422,
                detail="Provide either repository_id or repository_path, not both.",
            )
        repository_id = request.repository_id
        if request.repository_path is not None:
            try:
                repository = await RepositoryService(
                    registry=repositories(),
                    config=LocalRepositoryConfigStore(settings.local_config_path),
                ).register(Path(request.repository_path))
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            repository_id = repository.id
        try:
            run = await conversations().create_thread_run(
                thread_id=thread_id,
                goal=request.goal,
                intent=request.intent,
                mode=request.mode,
                run_intake=run_intake(),
                repository_id=repository_id,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Thread not found.") from error
        except MissingThreadRepositoryContext as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (RunIntakeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return run.model_dump(mode="json")

    @app.get("/threads/{thread_id}/runs")
    async def list_thread_runs(thread_id: UUID) -> list[dict[str, object]]:
        try:
            projections = await conversations().list_thread_runs(thread_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Thread not found.") from error
        return await _thread_run_projection_response(
            projections,
            getattr(app.state, "runtime", None),
        )

    @app.get("/runs")
    async def list_runs(
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, object]]:
        attributes = _api_attributes("GET", "/runs", 200)
        async with api_span("api.runs.list", attributes=attributes):
            return [
                run.model_dump(mode="json")
                for run in await runtime().list_runs(limit=limit)
            ]

    @app.post("/runs", status_code=201)
    async def create_run(request: CreateRunRequest) -> dict[str, object]:
        span_run_id = _NIL_RUN_ID
        attributes = _api_attributes("POST", "/runs", 201)
        async with api_span(
            "api.runs.create",
            run_id=lambda: span_run_id,
            attributes=attributes,
        ):
            try:
                run = await run_intake().create_run(
                    repository_id=request.repository_id,
                    goal=request.goal,
                    intent=request.intent,
                    mode=request.mode,
                )
            except KeyError as error:
                attributes["http.status_code"] = 404
                raise HTTPException(
                    status_code=404,
                    detail="Repository not found.",
                ) from error
            except (RunIntakeError, ValueError) as error:
                attributes["http.status_code"] = 409
                raise HTTPException(status_code=409, detail=str(error)) from error
            span_run_id = run.id
            attributes["run_id"] = str(run.id)
            return run.model_dump(mode="json")

    @app.post("/runtime/probes", status_code=201)
    async def create_probe(request: CreateProbeRequest) -> dict[str, object]:
        try:
            run = await run_intake().create_run(
                repository_id=request.repository_id,
                goal=request.goal,
                intent=RunIntent.READ_ONLY,
                execution_kind=ExecutionKind.RUNTIME_PROBE,
                runtime_route=RUNTIME_PROBE_ROUTE,
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
        attributes = _api_attributes("GET", "/runs/{run_id}", 200)
        attributes["run_id"] = str(run_id)
        async with api_span("api.runs.get", run_id=run_id, attributes=attributes):
            try:
                return (await runtime().get_run(run_id)).model_dump(mode="json")
            except KeyError as error:
                attributes["http.status_code"] = 404
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

    @app.get("/runs/{run_id}/children")
    async def list_children(run_id: UUID) -> list[dict[str, object]]:
        children = await runtime().repository.list_child_runs(run_id)
        return [child.model_dump(mode="json") for child in children]

    @app.get("/runs/{run_id}/descendants")
    async def list_descendants(run_id: UUID) -> list[dict[str, object]]:
        descendants = await runtime().repository.list_descendant_runs(run_id)
        return [descendant.model_dump(mode="json") for descendant in descendants]

    @app.get("/runs/{run_id}/team/assignments")
    async def list_team_assignments(
        run_id: UUID,
        all: bool = Query(default=False),
    ) -> list[dict[str, object]]:
        assignments = await team_repository_state().list_assignments(
            run_id,
            include_inactive=all,
        )
        resolver = CapabilityResolver()
        payloads: list[dict[str, object]] = []
        for assignment in assignments:
            policy = resolver.resolve_team_assignment(
                assignment,
                purpose=CapabilityPurpose.INSPECTION,
            )
            payload = assignment.model_dump(mode="json")
            payload.update(policy.as_inspection_payload())
            payloads.append(payload)
        return payloads

    @app.get("/runs/{run_id}/team/mailbox")
    async def list_team_mailbox(run_id: UUID) -> list[dict[str, object]]:
        messages = await team_repository_state().list_mailbox_messages(run_id)
        return [message.model_dump(mode="json") for message in messages]

    @app.post("/runs/{run_id}/team/assignments/{assignment_id}/retire")
    async def retire_team_assignment(
        run_id: UUID,
        assignment_id: UUID,
        reason: str = Query(default="retired_by_api"),
    ) -> dict[str, object]:
        assignment = await team_repository_state().retire_assignment(
            assignment_id,
            reason=reason,
        )
        if assignment.root_run_id != run_id:
            raise HTTPException(status_code=404, detail="Assignment not found.")
        return assignment.model_dump(mode="json")

    @app.get("/runs/{run_id}/events")
    async def stream_events(
        run_id: UUID,
        after_sequence: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        attributes = _api_attributes("GET", "/runs/{run_id}/events", 200)
        attributes["run_id"] = str(run_id)
        async with api_span("api.runs.events", run_id=run_id, attributes=attributes):
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
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
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
        attributes = _api_attributes("GET", "/runs/{run_id}/trace", 200)
        attributes["run_id"] = str(run_id)
        async with api_span("api.runs.trace", run_id=run_id, attributes=attributes):
            return [
                asdict(span)
                for span in await observability().list_spans_for_run(run_id)
            ]

    @app.get("/runs/{run_id}/metrics")
    async def list_metrics(run_id: UUID) -> list[dict[str, object]]:
        attributes = _api_attributes("GET", "/runs/{run_id}/metrics", 200)
        attributes["run_id"] = str(run_id)
        async with api_span("api.runs.metrics", run_id=run_id, attributes=attributes):
            return [
                asdict(metric)
                for metric in await observability().list_metrics_for_run(run_id)
            ]

    @app.get("/runs/{run_id}/model-calls")
    async def list_model_calls(run_id: UUID) -> list[dict[str, object]]:
        attributes = _api_attributes("GET", "/runs/{run_id}/model-calls", 200)
        attributes["run_id"] = str(run_id)
        async with api_span(
            "api.runs.model_calls",
            run_id=run_id,
            attributes=attributes,
        ):
            return [
                asdict(call)
                for call in await observability().list_model_calls_for_run(run_id)
            ]

    @app.get("/runs/{run_id}/diagnostics")
    async def get_run_diagnostics(run_id: UUID) -> dict[str, object]:
        attributes = _api_attributes("GET", "/runs/{run_id}/diagnostics", 200)
        attributes["run_id"] = str(run_id)
        async with api_span(
            "api.runs.diagnostics",
            run_id=run_id,
            attributes=attributes,
        ):
            diagnostics = RunDiagnosticsService(
                runtime_repository=runtime().repository,
                observability_repository=observability(),
                budget_repository=budgets(),
                tool_invocation_repository=tool_invocations(),
                validation_repository=validation_reports(),
                team_repository=team_repository_state(),
            )
            try:
                return (await diagnostics.summarize(run_id)).model_dump(mode="json")
            except KeyError as error:
                attributes["http.status_code"] = 404
                raise HTTPException(status_code=404, detail="Run not found.") from error

    @app.get("/runs/{run_id}/recovery-metrics")
    async def get_run_recovery_metrics(run_id: UUID) -> dict[str, object]:
        attributes = _api_attributes("GET", "/runs/{run_id}/recovery-metrics", 200)
        attributes["run_id"] = str(run_id)
        async with api_span(
            "api.runs.recovery_metrics",
            run_id=run_id,
            attributes=attributes,
        ):
            recovery_metrics = RecoveryMetricsService(
                runtime_repository=runtime().repository,
                observability_repository=observability(),
                budget_repository=budgets(),
                validation_repository=validation_reports(),
                team_repository=team_repository_state(),
            )
            try:
                return (await recovery_metrics.report_for_run(run_id)).model_dump(
                    mode="json"
                )
            except KeyError as error:
                attributes["http.status_code"] = 404
                raise HTTPException(status_code=404, detail="Run not found.") from error

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
                runtime_route=compaction.runtime_route,
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


async def _conversation_sse(
    events: AsyncIterator[ConversationStreamEvent],
) -> AsyncIterator[str]:
    async for event in events:
        data = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
        yield f"id: {event.sequence}\nevent: {event.event.value}\ndata: {data}\n\n"


def _format_sse(event: RuntimeEvent) -> str:
    data = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
    return f"id: {event.sequence}\nevent: {event.event_type.value}\ndata: {data}\n\n"


def _api_attributes(
    method: str,
    route: str,
    status_code: int,
) -> dict[str, object]:
    return {
        "http.method": method,
        "http.route": route,
        "http.status_code": status_code,
    }


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


async def _thread_run_projection_response(
    projections: list[dict[str, object]],
    runtime_service: object | None,
) -> list[dict[str, object]]:
    get_run = getattr(runtime_service, "get_run", None)
    list_artifacts = getattr(runtime_service, "list_artifacts", None)
    if not callable(get_run) or not callable(list_artifacts):
        return projections
    enriched: list[dict[str, object]] = []
    for projection in projections:
        item = dict(projection)
        try:
            run_id = UUID(str(item["run_id"]))
            run = await get_run(run_id)
            artifacts = await list_artifacts(run_id)
        except (KeyError, TypeError, ValueError):
            enriched.append(item)
            continue
        item["status"] = run.status.value
        item["result_text"] = run.result_text
        item["artifacts"] = [artifact.model_dump(mode="json") for artifact in artifacts]
        enriched.append(item)
    return enriched


app = create_app()
