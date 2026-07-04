from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path
from time import monotonic
from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.middleware.base import RequestResponseEndpoint

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.api.schemas import (
    ApprovalDecisionRequest,
    BudgetLedgerResponse,
    ConfigStatusResponse,
    ContextCompactionResponse,
    ContinueConversationTurnRequest,
    CreateConversationTurnRequest,
    CreateProbeRequest,
    CreateThreadMessageRequest,
    CreateThreadRequest,
    DispatchResponse,
    ErrorResponse,
    ExtensionSkillsResponse,
    HealthCheckResponse,
    McpServersResponse,
    MemoryDeleteResponse,
    MemoryEntriesResponse,
    MemoryEntryResponse,
    MemoryStatusResponse,
    ModelProfileResponse,
    ReadinessReportResponse,
    SurfaceToolsResponse,
    ThreadArtifactsResponse,
    ThreadAttachmentResponse,
    ThreadAttachmentsResponse,
    ThreadUploadsResponse,
    ThreadUsageResponse,
    UpdateThreadSettingsRequest,
    WorkspaceCandidateResponse,
    WorkspaceCleanupRequest,
)
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.attachments.models import (
    AttachmentError,
    AttachmentScope,
    AttachmentSource,
    AttachmentStatus,
    ThreadAttachment,
)
from awesome_agent.attachments.service import AttachmentService
from awesome_agent.attachments.store import AttachmentContentStore
from awesome_agent.conversation.events import ConversationStreamEvent
from awesome_agent.conversation.intake import ConversationRunIntakeService
from awesome_agent.conversation.repository import ConversationRepository
from awesome_agent.conversation.service import ConversationService
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
from awesome_agent.memory.builtin import BuiltinMemoryStore
from awesome_agent.memory.external import NoopMemoryProvider
from awesome_agent.memory.models import MemoryTarget
from awesome_agent.memory.policy import MemoryPolicy
from awesome_agent.memory.service import MemoryService
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
from awesome_agent.persistence.attachments import PostgresAttachmentRepository
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
from awesome_agent.persistence.local_attachments import LocalAttachmentRepository
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
from awesome_agent.repositories.config import LocalRepositoryConfigStore
from awesome_agent.repositories.registry import RepositoryRegistry
from awesome_agent.repositories.worktrees import ManagedRunWorktreeManager
from awesome_agent.runtime.asyncio import configure_event_loop_policy
from awesome_agent.runtime.capabilities import CapabilityPurpose, CapabilityResolver
from awesome_agent.runtime.diagnostics import RunDiagnosticsService
from awesome_agent.runtime.dispatch import DispatchConflict
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.intake import RunIntakeError, RunIntakeService
from awesome_agent.runtime.probe_graph import RUNTIME_PROBE_ROUTE
from awesome_agent.runtime.recovery_metrics import RecoveryMetricsService
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.service import RuntimeService
from awesome_agent.runtime.workspaces import (
    WorkspaceCandidate,
    WorkspaceRetentionService,
    parse_workspace_age,
)
from awesome_agent.runtime.workspaces import (
    WorkspaceCleanupRequest as RuntimeWorkspaceCleanupRequest,
)
from awesome_agent.safety.redaction import (
    install_redacting_log_filter,
    redact_text,
    redact_value,
)
from awesome_agent.settings import Settings
from awesome_agent.surfaces.capabilities import CapabilitySurfaceService
from awesome_agent.surfaces.client import changed_file_summaries_from_payload
from awesome_agent.tools.repository import build_modifying_registry

logger = logging.getLogger(__name__)
_NIL_RUN_ID = UUID(int=0)
_REQUEST_ID_HEADER = "x-request-id"

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
    memory_service: MemoryService | None = None,
    attachment_service: AttachmentService | None = None,
) -> FastAPI:
    install_redacting_log_filter(logger)
    settings = settings or Settings()
    threads_repository = thread_repository or InMemoryConversationRepository()
    default_runtime_repository = getattr(service, "repository", None)
    if default_runtime_repository is None:
        default_runtime_repository = InMemoryRuntimeRepository()
    default_event_stream = EventStream()
    configured_memory_service = memory_service or MemoryService(
        builtin=BuiltinMemoryStore(
            root=settings.local_state_dir / "memory",
            policy=MemoryPolicy(),
        ),
        provider=NoopMemoryProvider(),
        builtin_enabled=settings.builtin_memory_enabled,
        provider_enabled=settings.mem0_enabled,
    )
    default_attachment_service = attachment_service or AttachmentService(
        repository=LocalAttachmentRepository(
            settings.local_state_dir / "awesome-agent.db"
        ),
        store=AttachmentContentStore(settings.local_state_dir / "attachments"),
    )
    active_extension_catalog = extension_catalog
    if active_extension_catalog is None:
        active_extension_catalog = build_project_extension_catalog_sync(project_root)
    extension_catalogs_by_version = {
        catalog.version: catalog
        for catalog in [*(extension_catalog_history or []), active_extension_catalog]
    }
    default_conversation_intake = ConversationRunIntakeService(
        conversations=threads_repository,
        runtime=default_runtime_repository,
        events=default_event_stream,
        default_model=settings.leader_model,
        extension_catalog_version=active_extension_catalog.version,
        attachment_service=default_attachment_service,
    )
    default_conversation_service = conversation_service or ConversationService(
        repository=threads_repository,
        runtime_repository=default_runtime_repository,
        conversation_run_intake=default_conversation_intake,
        default_model=settings.leader_model,
        event_poll_interval=settings.event_poll_interval_seconds,
        global_builtin_memory_enabled=settings.builtin_memory_enabled,
        global_provider_memory_enabled=settings.mem0_enabled,
    )
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
            app.state.memory_service = configured_memory_service
            app.state.attachment_service = default_attachment_service
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
        configured_attachment_service = attachment_service or AttachmentService(
            repository=PostgresAttachmentRepository(sessions),
            store=AttachmentContentStore(settings.local_state_dir / "attachments"),
        )
        conversation_intake = ConversationRunIntakeService(
            conversations=app.state.threads,
            runtime=runtime_repository,
            events=event_stream,
            default_model=settings.leader_model,
            extension_catalog_version=active_extension_catalog.version,
            attachment_service=configured_attachment_service,
        )
        app.state.conversations = conversation_service or ConversationService(
            repository=app.state.threads,
            runtime_repository=runtime_repository,
            conversation_run_intake=conversation_intake,
            default_model=settings.leader_model,
            event_poll_interval=settings.event_poll_interval_seconds,
            global_builtin_memory_enabled=settings.builtin_memory_enabled,
            global_provider_memory_enabled=settings.mem0_enabled,
        )
        app.state.memory_service = configured_memory_service
        app.state.attachment_service = configured_attachment_service
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

    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = request.headers.get(_REQUEST_ID_HEADER) or uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request,
        error: HTTPException,
    ) -> JSONResponse:
        return _structured_error_response(
            request,
            status_code=error.status_code,
            detail=error.detail,
            headers=dict(error.headers) if error.headers is not None else None,
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        return _structured_error_response(
            request,
            status_code=422,
            detail=str(error),
            code="validation_error",
            hint="Check request path, query parameters, and JSON body shape.",
            recoverable=False,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        logger.exception("Unhandled API request failed.", exc_info=error)
        return _structured_error_response(
            request,
            status_code=500,
            detail="Internal server error.",
            code="internal_error",
            hint="Check API logs with the returned request_id.",
            recoverable=True,
        )

    if service is not None:
        app.state.runtime = service
    if intake is not None:
        app.state.intake = intake
    if registry is not None:
        app.state.registry = registry
    app.state.extension_catalog = active_extension_catalog
    app.state.threads = threads_repository
    app.state.conversations = default_conversation_service
    app.state.memory_service = configured_memory_service
    app.state.attachment_service = default_attachment_service
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

    def memory() -> MemoryService:
        return cast(MemoryService, app.state.memory_service)

    def attachments() -> AttachmentService:
        return cast(AttachmentService, app.state.attachment_service)

    @app.get("/health")
    async def health() -> dict[str, str]:
        async with api_span(
            "api.health",
            attributes=_api_attributes("GET", "/health", 200),
        ):
            return {"status": "ok"}

    @app.get("/extensions/catalog")
    async def get_extensions_catalog() -> dict[str, object]:
        return _redacted_dict(extensions_catalog().model_dump(mode="json"))

    @app.get("/extensions/diagnostics")
    async def get_extensions_diagnostics() -> dict[str, object]:
        diagnostics = ExtensionDiagnosticsService(
            active_catalog=extensions_catalog(),
            runtime_repository=runtime().repository,
            tool_invocation_repository=tool_invocations(),
        )
        return cast(
            dict[str, object],
            _redacted_payload((await diagnostics.summarize()).model_dump(mode="json")),
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
            _redacted_payload(
                diff_extension_catalogs(before, after).model_dump(mode="json")
            ),
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

    @app.get("/models")
    async def list_model_profiles() -> list[ModelProfileResponse]:
        return _model_profiles(settings)

    @app.get("/surface/tools")
    async def get_surface_tools() -> SurfaceToolsResponse:
        return SurfaceToolsResponse.model_validate(
            CapabilitySurfaceService(
                catalog=extensions_catalog(),
                tool_registry=build_modifying_registry(),
            ).tools()
        )

    @app.get("/extensions/skills")
    async def get_extension_skills() -> ExtensionSkillsResponse:
        skills = CapabilitySurfaceService(
            catalog=extensions_catalog(),
            tool_registry=build_modifying_registry(),
        ).skills()
        return ExtensionSkillsResponse(configured=bool(skills), items=skills)

    @app.get("/extensions/mcp")
    async def get_mcp_status() -> McpServersResponse:
        sources = CapabilitySurfaceService(
            catalog=extensions_catalog(),
            tool_registry=build_modifying_registry(),
        ).mcp_servers()
        return McpServersResponse(configured=bool(sources), items=sources)

    @app.get("/memory")
    async def get_memory_status() -> MemoryStatusResponse:
        return MemoryStatusResponse.model_validate(
            memory().status().model_dump(mode="json")
        )

    @app.get("/memory/entries")
    async def list_memory_entries(target: str | None = None) -> MemoryEntriesResponse:
        parsed = MemoryTarget(target) if target is not None else None
        result = await memory().list_entries(target=parsed)
        return MemoryEntriesResponse(
            target=target,
            items=[
                MemoryEntryResponse.model_validate(entry.model_dump(mode="json"))
                for entry in result.entries
            ],
        )

    @app.delete("/memory/entries/{memory_id}")
    async def delete_memory_entry(
        memory_id: str,
        target: str,
    ) -> MemoryDeleteResponse:
        parsed = MemoryTarget(target)
        result = await memory().delete(
            target=parsed,
            memory_id=memory_id,
            run_id=None,
            agent_id=None,
        )
        if result.status == "not_found":
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "memory_entry_not_found",
                    "message": f"Memory entry not found: {memory_id}",
                },
            )
        return MemoryDeleteResponse(
            status=result.status,
            memory_id=memory_id,
            target=parsed.value,
        )

    @app.get("/config")
    async def get_config_status() -> ConfigStatusResponse:
        return ConfigStatusResponse(
            api_host=settings.api_host,
            local_config_path=str(settings.local_config_path),
            artifact_root=str(settings.artifact_root),
            workspace_root=(
                str(settings.workspace_root)
                if settings.workspace_root is not None
                else None
            ),
            sandbox_backend=settings.sandbox_backend,
            local_cli_sandbox_backend=settings.local_cli_sandbox_backend,
            observability_enabled=settings.observability_enabled,
            deepseek_api_key_configured=settings.deepseek_api_key is not None,
            mem0_api_key_configured=settings.mem0_api_key is not None,
        )

    @app.post("/threads")
    async def create_thread(request: CreateThreadRequest) -> dict[str, object]:
        thread = await threads().create_thread(
            title=request.title,
            context_kind=request.context_kind,
            context_path=request.context_path,
            repository_id=request.repository_id,
            default_model=request.default_model,
            sandbox_profile=request.sandbox_profile,
            local_memory_enabled=(
                request.local_memory_enabled
                if request.local_memory_enabled is not None
                else settings.builtin_memory_enabled
            ),
            provider_memory=(
                request.provider_memory
                if request.provider_memory is not None
                else ("mem0" if settings.mem0_enabled else None)
            ),
        )
        return _redacted_dict(thread.api_payload())

    @app.get("/threads")
    async def list_threads() -> list[dict[str, object]]:
        return [
            await _thread_payload_with_changed_files(threads(), thread.api_payload())
            for thread in await threads().list_threads()
        ]

    @app.get("/threads/resolve")
    async def resolve_thread(query: str) -> dict[str, object]:
        try:
            thread = await threads().resolve_thread(query)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Thread not found.") from error
        return _redacted_dict(thread.api_payload())

    @app.get("/threads/{thread_id:uuid}")
    async def get_thread(thread_id: UUID) -> dict[str, object]:
        try:
            thread = await threads().get_thread(thread_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Thread not found.") from error
        return _redacted_dict(thread.api_payload())

    @app.patch("/threads/{thread_id}/settings")
    async def update_thread_settings(
        thread_id: UUID,
        request: UpdateThreadSettingsRequest,
    ) -> dict[str, object]:
        try:
            thread = await threads().update_thread_settings(
                thread_id,
                default_model=request.default_model,
                thinking_mode=request.thinking_mode,
                local_memory_enabled=request.local_memory_enabled,
                provider_memory=request.provider_memory,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Thread not found.") from error
        return _redacted_dict(thread.api_payload())

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
        return _redacted_dict(message.model_dump(mode="json"))

    @app.get("/threads/{thread_id}/messages")
    async def list_thread_messages(thread_id: UUID) -> list[dict[str, object]]:
        try:
            messages = await threads().list_messages(thread_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Thread not found.") from error
        return [_redacted_dict(message.model_dump(mode="json")) for message in messages]

    @app.post("/threads/{thread_id}/attachments")
    async def create_thread_attachment(
        thread_id: UUID,
        request: Request,
    ) -> ThreadAttachmentResponse:
        try:
            await threads().get_thread(thread_id)
            upload = await _read_multipart_attachment(request)
            attachment = await attachments().create(
                thread_id=thread_id,
                filename=cast(str, upload["filename"]),
                content=cast(bytes, upload["content"]),
                mime_type=cast(str | None, upload["mime_type"]),
                source=AttachmentSource.API,
                scope=AttachmentScope(str(upload["scope"])),
            )
            return _attachment_response(attachment)
        except Exception as error:
            raise _attachment_http_error(error) from error

    @app.get("/threads/{thread_id}/attachments")
    async def list_thread_attachments(
        thread_id: UUID,
        status: str | None = None,
        include_deleted: bool = False,
        limit: int = Query(default=50, ge=1, le=50),
    ) -> ThreadAttachmentsResponse:
        try:
            await threads().get_thread(thread_id)
            parsed_status = AttachmentStatus(status) if status is not None else None
            items = await attachments().list_thread(
                thread_id,
                status=parsed_status,
                include_deleted=include_deleted,
                limit=limit,
            )
            return ThreadAttachmentsResponse(
                thread_id=thread_id,
                items=[_attachment_response(item) for item in items],
            )
        except Exception as error:
            raise _attachment_http_error(error) from error

    @app.get("/threads/{thread_id}/attachments/{attachment_id}")
    async def get_thread_attachment(
        thread_id: UUID,
        attachment_id: UUID,
    ) -> ThreadAttachmentResponse:
        try:
            return _attachment_response(
                await attachments().get(
                    thread_id=thread_id,
                    attachment_id=attachment_id,
                )
            )
        except Exception as error:
            raise _attachment_http_error(error) from error

    @app.get("/threads/{thread_id}/attachments/{attachment_id}/content")
    async def get_thread_attachment_content(
        thread_id: UUID,
        attachment_id: UUID,
    ) -> Response:
        try:
            attachment = await attachments().get(
                thread_id=thread_id,
                attachment_id=attachment_id,
            )
            if attachment.status is AttachmentStatus.DELETED:
                raise ValueError("attachment_content_deleted")
            content = attachments().store.read_bytes(attachment.storage_path)
            return Response(
                content=content,
                media_type=attachment.mime_type,
                headers={
                    "content-disposition": (
                        f'attachment; filename="{attachment.filename}"'
                    )
                },
            )
        except Exception as error:
            raise _attachment_http_error(error) from error

    @app.delete("/threads/{thread_id}/attachments/{attachment_id}")
    async def delete_thread_attachment(
        thread_id: UUID,
        attachment_id: UUID,
    ) -> ThreadAttachmentResponse:
        try:
            return _attachment_response(
                await attachments().delete(
                    thread_id=thread_id,
                    attachment_id=attachment_id,
                )
            )
        except Exception as error:
            raise _attachment_http_error(error) from error

    @app.get("/threads/{thread_id}/uploads")
    async def list_thread_uploads(thread_id: UUID) -> ThreadUploadsResponse:
        try:
            await threads().get_thread(thread_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Thread not found.") from error
        items = await attachments().list_thread(thread_id, include_deleted=False)
        return ThreadUploadsResponse(
            thread_id=thread_id,
            configured=True,
            items=[item.model_dump(mode="json") for item in items],
        )

    @app.get("/threads/{thread_id}/artifacts")
    async def list_thread_artifacts(thread_id: UUID) -> ThreadArtifactsResponse:
        try:
            run_ids = await _thread_run_ids(conversations(), thread_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Thread not found.") from error
        return ThreadArtifactsResponse(
            thread_id=thread_id,
            items=await _thread_artifact_items(
                run_ids, getattr(app.state, "runtime", None)
            ),
        )

    @app.get("/threads/{thread_id}/usage")
    async def get_thread_usage(thread_id: UUID) -> ThreadUsageResponse:
        try:
            run_ids = await _thread_run_ids(conversations(), thread_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Thread not found.") from error
        latest_run_id = run_ids[0] if run_ids else None
        if latest_run_id is None or budgets() is None:
            return ThreadUsageResponse(thread_id=thread_id)
        ledger = await budgets().get_ledger(latest_run_id)  # type: ignore[union-attr]
        return ThreadUsageResponse(
            thread_id=thread_id,
            run_id=latest_run_id,
            input_tokens=ledger.total_input_tokens,
            output_tokens=ledger.total_output_tokens,
            total_tokens=ledger.total_input_tokens + ledger.total_output_tokens,
            reasoning_tokens=ledger.total_reasoning_tokens,
            active_seconds=ledger.active_seconds,
            model_call_count=ledger.model_call_count,
            threshold_status=ledger.threshold_status,
        )

    @app.post("/threads/{thread_id}/turns/stream")
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
                    thinking=request.thinking_mode,
                    memory=request.memory,
                    skill_ids=tuple(request.skill_ids),
                    attachment_ids=tuple(request.attachment_ids),
                )
            ),
            media_type="text/event-stream",
        )

    @app.post("/threads/{thread_id}/turns/continue/stream")
    async def continue_conversation_turn(
        thread_id: UUID,
        request: ContinueConversationTurnRequest,
        http_request: Request,
    ) -> StreamingResponse:
        try:
            run = await conversations().continuable_thread_run(
                thread_id,
                expected_run_id=request.expected_run_id,
            )
            if run is None:
                if request.expected_run_id is not None:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "resumable_run_changed",
                            "message": "The requested Run is not continuable.",
                        },
                    )
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "no_resumable_turn",
                        "message": "No resumable turn is available for this thread.",
                    },
                )
            header_after_sequence = _last_event_id_sequence(http_request)
            after_sequence = max(request.after_sequence, header_after_sequence)
            events = conversations().continue_turn(
                thread_id=thread_id,
                expected_run_id=request.expected_run_id,
                after_sequence=after_sequence,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Thread not found.") from error
        return StreamingResponse(
            _conversation_sse(events),
            media_type="text/event-stream",
        )

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

    @app.post("/threads/{thread_id}/runs/{run_id}/cancel")
    async def cancel_thread_run(thread_id: UUID, run_id: UUID) -> dict[str, object]:
        await _assert_run_belongs_to_thread(conversations(), thread_id, run_id)
        try:
            run = await runtime().cancel_run(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error
        except DispatchConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _redacted_dict(run.model_dump(mode="json"))

    @app.get("/threads/{thread_id}/runs/{run_id}/approvals")
    async def list_thread_run_approvals(
        thread_id: UUID,
        run_id: UUID,
    ) -> list[dict[str, object]]:
        await _assert_run_belongs_to_thread(conversations(), thread_id, run_id)
        return [
            _redacted_dict(event.model_dump(mode="json"))
            for event in await runtime().list_events(run_id)
            if event.event_type.value.startswith("approval.")
        ]

    @app.post("/threads/{thread_id}/runs/{run_id}/approvals/{approval_id}")
    async def decide_thread_run_approval(
        thread_id: UUID,
        run_id: UUID,
        approval_id: UUID,
        request: ApprovalDecisionRequest,
    ) -> dict[str, object]:
        await _assert_run_belongs_to_thread(conversations(), thread_id, run_id)
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
        return _redacted_dict(event.model_dump(mode="json"))

    @app.get("/runs")
    async def list_runs(
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, object]]:
        attributes = _api_attributes("GET", "/runs", 200)
        async with api_span("api.runs.list", attributes=attributes):
            return [
                _redacted_dict(run.model_dump(mode="json"))
                for run in await runtime().list_runs(limit=limit)
            ]

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
        return _redacted_dict(run.model_dump(mode="json"))

    @app.get("/repositories")
    async def list_repositories() -> list[dict[str, object]]:
        return [
            _redacted_dict(repository.model_dump(mode="json"))
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
        return _redacted_dict(repository.model_dump(mode="json"))

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
                return _redacted_dict(
                    (await runtime().get_run(run_id)).model_dump(mode="json")
                )
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
            last_release_reason=(
                redact_text(run.last_release_reason).text
                if run.last_release_reason is not None
                else None
            ),
            last_error=(
                redact_text(run.last_dispatch_error).text
                if run.last_dispatch_error is not None
                else None
            ),
        )

    @app.get("/runs/{run_id}/agents")
    async def list_agents(run_id: UUID) -> list[dict[str, object]]:
        return [
            _redacted_dict(agent.model_dump(mode="json"))
            for agent in await runtime().list_agents(run_id)
        ]

    @app.get("/runs/{run_id}/todos")
    async def list_todos(run_id: UUID) -> list[dict[str, object]]:
        return [
            _redacted_dict(todo.model_dump(mode="json"))
            for todo in await runtime().list_todos(run_id)
        ]

    @app.get("/runs/{run_id}/events/history")
    async def event_history(
        run_id: UUID,
        after_sequence: int = Query(default=0, ge=0),
    ) -> list[dict[str, object]]:
        return [
            _redacted_dict(event.model_dump(mode="json"))
            for event in await runtime().list_events(
                run_id,
                after_sequence=after_sequence,
            )
        ]

    @app.get("/runs/{run_id}/children")
    async def list_children(run_id: UUID) -> list[dict[str, object]]:
        children = await runtime().repository.list_child_runs(run_id)
        return [_redacted_dict(child.model_dump(mode="json")) for child in children]

    @app.get("/runs/{run_id}/descendants")
    async def list_descendants(run_id: UUID) -> list[dict[str, object]]:
        descendants = await runtime().repository.list_descendant_runs(run_id)
        return [
            _redacted_dict(descendant.model_dump(mode="json"))
            for descendant in descendants
        ]

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
            payloads.append(_redacted_dict(payload))
        return payloads

    @app.get("/runs/{run_id}/team/mailbox")
    async def list_team_mailbox(run_id: UUID) -> list[dict[str, object]]:
        messages = await team_repository_state().list_mailbox_messages(run_id)
        return [_redacted_dict(message.model_dump(mode="json")) for message in messages]

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
        return _redacted_dict(assignment.model_dump(mode="json"))

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
            _redacted_dict(event.model_dump(mode="json"))
            for event in await runtime().list_events(run_id)
            if event.event_type.value == "message.created"
        ]

    @app.get("/runs/{run_id}/artifacts")
    async def list_artifacts(run_id: UUID) -> list[dict[str, object]]:
        return [
            _redacted_dict(artifact.model_dump(mode="json"))
            for artifact in await runtime().list_artifacts(run_id)
        ]

    @app.get("/artifacts/{artifact_id}")
    async def download_artifact(artifact_id: UUID) -> Response:
        try:
            artifact = await runtime().get_artifact(artifact_id)
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="Artifact not found."
            ) from error
        path = Path(artifact.path)
        if _is_text_artifact(artifact.mime_type, path):
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return FileResponse(
                    path,
                    media_type=artifact.mime_type,
                    filename=path.name,
                )
            return Response(
                content=redact_text(content).text,
                media_type=artifact.mime_type,
                headers={"content-disposition": _attachment_header(path.name)},
            )
        return FileResponse(
            path,
            media_type=artifact.mime_type,
            filename=path.name,
        )

    @app.get("/runs/{run_id}/approvals")
    async def list_approvals(run_id: UUID) -> list[dict[str, object]]:
        return [
            _redacted_dict(event.model_dump(mode="json"))
            for event in await runtime().list_events(run_id)
            if event.event_type.value.startswith("approval.")
        ]

    @app.get("/runs/{run_id}/verification")
    async def list_verification(run_id: UUID) -> list[dict[str, object]]:
        repository = validation_reports()
        if repository is None:
            return []
        return [
            _redacted_dict(_verification_report_response(report))
            for report in await repository.list_for_run(run_id)
        ]

    @app.get("/runs/{run_id}/trace")
    async def list_trace(run_id: UUID) -> list[dict[str, object]]:
        attributes = _api_attributes("GET", "/runs/{run_id}/trace", 200)
        attributes["run_id"] = str(run_id)
        async with api_span("api.runs.trace", run_id=run_id, attributes=attributes):
            return [
                _redacted_dict(asdict(span))
                for span in await observability().list_spans_for_run(run_id)
            ]

    @app.get("/runs/{run_id}/metrics")
    async def list_metrics(run_id: UUID) -> list[dict[str, object]]:
        attributes = _api_attributes("GET", "/runs/{run_id}/metrics", 200)
        attributes["run_id"] = str(run_id)
        async with api_span("api.runs.metrics", run_id=run_id, attributes=attributes):
            return [
                _redacted_dict(asdict(metric))
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
                _redacted_dict(asdict(call))
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
                return _redacted_dict(
                    (await diagnostics.summarize(run_id)).model_dump(mode="json")
                )
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
                return _redacted_dict(
                    (await recovery_metrics.report_for_run(run_id)).model_dump(
                        mode="json"
                    )
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
        data = json.dumps(
            _redacted_payload(event.model_dump(mode="json")),
            separators=(",", ":"),
        )
        yield f"id: {event.sequence}\nevent: {event.event.value}\ndata: {data}\n\n"


def _structured_error_response(
    request: Request,
    *,
    status_code: int,
    detail: object,
    code: str | None = None,
    hint: str | None = None,
    recoverable: bool | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    message = redact_text(_error_message(detail)).text
    classified_code = (
        code
        or _error_code(detail)
        or _classify_error(
            status_code,
            message,
        )
    )
    request_id = str(getattr(request.state, "request_id", "") or uuid4().hex)
    payload = ErrorResponse(
        code=classified_code,
        message=message,
        detail=message,
        hint=hint or _error_hint(classified_code, status_code),
        request_id=request_id,
        trace_id=request.headers.get("traceparent"),
        recoverable=(
            recoverable if recoverable is not None else _is_recoverable(classified_code)
        ),
    )
    response_headers = dict(headers or {})
    response_headers[_REQUEST_ID_HEADER] = request_id
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers=response_headers,
    )


def _attachment_response(attachment: ThreadAttachment) -> ThreadAttachmentResponse:
    return ThreadAttachmentResponse.model_validate(attachment.model_dump(mode="json"))


async def _read_multipart_attachment(request: Request) -> dict[str, object]:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        raise ValueError("invalid_attachment_content_type")
    body = await request.body()
    message = BytesParser(policy=email_policy).parsebytes(
        (f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode() + body
    )
    filename = None
    mime_type = None
    content = None
    scope = AttachmentScope.NEXT_TURN.value
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name == "scope":
            raw_payload = part.get_payload(decode=True)
            payload = raw_payload if isinstance(raw_payload, bytes) else b""
            scope = payload.decode("utf-8").strip() or scope
        if name == "file":
            filename = part.get_filename() or "attachment"
            mime_type = part.get_content_type()
            raw_content = part.get_payload(decode=True)
            content = raw_content if isinstance(raw_content, bytes) else b""
    if content is None:
        raise ValueError("attachment_not_found")
    return {
        "filename": filename or "attachment",
        "mime_type": mime_type,
        "content": content,
        "scope": scope,
    }


def _attachment_http_error(error: Exception) -> HTTPException:
    if isinstance(error, HTTPException):
        return error
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail="attachment_not_found")
    code = None
    if isinstance(error, AttachmentError):
        code = error.code
    elif isinstance(error, ValueError) and error.args:
        code = str(error.args[0])
    status = {
        "attachment_too_large": 413,
        "too_many_pending_attachments": 409,
        "too_many_turn_attachments": 409,
        "attachment_not_found": 404,
        "attachment_thread_mismatch": 404,
        "attachment_content_deleted": 410,
        "attachment_deleted": 410,
        "invalid_attachment_filename": 422,
        "invalid_attachment_path": 422,
        "unsupported_attachment_scope": 422,
        "attachment_not_pending": 422,
        "attachment_not_text_like": 422,
        "attachment_read_out_of_range": 422,
        "attachment_not_bound_to_run": 422,
        "invalid_attachment_content_type": 422,
    }.get(code or "", 500)
    return HTTPException(
        status_code=status,
        detail={
            "code": code or "attachment_error",
            "message": str(error),
        },
    )


def _error_message(detail: object) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("detail")
        if isinstance(message, str):
            return message
    return str(detail)


def _error_code(detail: object) -> str | None:
    if not isinstance(detail, dict):
        return None
    code = detail.get("code")
    return code if isinstance(code, str) and code else None


def _classify_error(status_code: int, message: str) -> str:
    normalized = message.casefold()
    if status_code == 404:
        return "not_found"
    if status_code == 422:
        return "validation_error"
    if status_code in {401, 403}:
        return "permission_error"
    if "mcp" in normalized:
        return "mcp_error"
    if "sandbox" in normalized or "aio" in normalized:
        return "sandbox_error"
    if "model" in normalized or "provider" in normalized or "api key" in normalized:
        return "model_error"
    if "config" in normalized or "configured" in normalized:
        return "config_error"
    if "repository" in normalized or "worktree" in normalized:
        return "repository_error"
    if status_code == 409:
        return "conflict"
    if status_code >= 500:
        return "internal_error"
    return "request_error"


def _error_hint(code: str, status_code: int) -> str | None:
    if code == "model_error":
        return "Check model configuration and provider API key environment variables."
    if code == "sandbox_error":
        return "Check sandbox configuration and sandbox service health."
    if code == "mcp_error":
        return "Check configured MCP servers and their health status."
    if code == "config_error":
        return "Run awesome doctor and verify resolved config paths."
    if code == "validation_error":
        return "Check request path, query parameters, and JSON body shape."
    if code == "repository_error":
        return "Register or bind a valid repository context before starting a Run."
    if code == "not_found":
        return "Verify the requested resource id still exists."
    if status_code >= 500:
        return "Check API logs with the returned request_id."
    return None


def _is_recoverable(code: str) -> bool:
    return code in {
        "conflict",
        "model_error",
        "sandbox_error",
        "mcp_error",
        "config_error",
        "internal_error",
    }


def _format_sse(event: RuntimeEvent) -> str:
    data = json.dumps(
        _redacted_payload(event.model_dump(mode="json")),
        separators=(",", ":"),
    )
    return f"id: {event.sequence}\nevent: {event.event_type.value}\ndata: {data}\n\n"


def _redacted_payload(value: object) -> object:
    redacted, _report = redact_value(value)
    return redacted


def _redacted_dict(value: Mapping[str, object]) -> dict[str, object]:
    redacted = _redacted_payload(value)
    if isinstance(redacted, Mapping):
        return {str(key): item for key, item in redacted.items()}
    return {}


def _last_event_id_sequence(request: Request) -> int:
    value = request.headers.get("last-event-id")
    if value is None or not value.isdigit():
        return 0
    return int(value)


def _is_text_artifact(mime_type: str, path: Path) -> bool:
    if mime_type.startswith("text/"):
        return True
    if mime_type in {"application/json", "application/xml", "application/yaml"}:
        return True
    return path.suffix.lower() in {".json", ".md", ".txt", ".xml", ".yaml", ".yml"}


def _attachment_header(filename: str) -> str:
    safe = filename.replace("\\", "_").replace("/", "_").replace('"', "")
    return f'attachment; filename="{safe}"'


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


def _model_profiles(settings: Settings) -> list[ModelProfileResponse]:
    roles = [
        ("leader", settings.leader_model),
        ("teammate", settings.teammate_model),
        ("verifier", settings.verifier_model),
        ("subagent", settings.subagent_model),
    ]
    return [
        ModelProfileResponse(
            role=role,
            name=model,
            provider="deepseek",
            configured=settings.deepseek_api_key is not None,
            api_key_env="AWESOME_AGENT_DEEPSEEK_API_KEY",
            api_key_present=settings.deepseek_api_key is not None,
            base_url=settings.deepseek_base_url,
            source="settings",
            overridden_by_env=False,
        )
        for role, model in roles
    ]


async def _thread_run_ids(
    conversation_service: ConversationService,
    thread_id: UUID,
) -> list[UUID]:
    projections = await conversation_service.list_thread_runs(thread_id)
    run_ids: list[UUID] = []
    for projection in projections:
        try:
            run_ids.append(UUID(str(projection["run_id"])))
        except (KeyError, TypeError, ValueError):
            continue
    return run_ids


async def _assert_run_belongs_to_thread(
    conversation_service: ConversationService,
    thread_id: UUID,
    run_id: UUID,
) -> None:
    try:
        projections = await conversation_service.list_thread_runs(thread_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Thread not found.") from error
    if not any(
        str(projection.get("run_id") or projection.get("id")) == str(run_id)
        for projection in projections
    ):
        raise HTTPException(status_code=404, detail="Run not found for thread.")


async def _thread_artifact_items(
    run_ids: list[UUID],
    runtime_service: object | None,
) -> list[dict[str, object]]:
    list_artifacts = getattr(runtime_service, "list_artifacts", None)
    if not callable(list_artifacts):
        return []
    items: list[dict[str, object]] = []
    for run_id in run_ids:
        try:
            artifacts = await list_artifacts(run_id)
        except (KeyError, TypeError, ValueError):
            continue
        items.extend(
            _redacted_dict(artifact.model_dump(mode="json")) for artifact in artifacts
        )
    return items


async def _thread_payload_with_changed_files(
    repository: ConversationRepository,
    payload: dict[str, object],
) -> dict[str, object]:
    enriched = dict(payload)
    try:
        thread_id = UUID(str(payload["id"]))
        messages = await repository.list_messages(thread_id)
    except (KeyError, TypeError, ValueError):
        return _redacted_dict(enriched)
    for message in reversed(messages):
        changed_files = changed_file_summaries_from_payload(
            message.metadata.get("changed_files")
        )
        if not changed_files:
            continue
        enriched["changed_file_count"] = len(changed_files)
        enriched["latest_changed_files"] = [
            {
                "path": item.path,
                "status": item.status,
                "display_path": item.visible_path,
            }
            for item in changed_files
        ]
        break
    return _redacted_dict(enriched)


async def _thread_run_projection_response(
    projections: list[dict[str, object]],
    runtime_service: object | None,
) -> list[dict[str, object]]:
    get_run = getattr(runtime_service, "get_run", None)
    list_artifacts = getattr(runtime_service, "list_artifacts", None)
    if not callable(get_run) or not callable(list_artifacts):
        return [_redacted_dict(projection) for projection in projections]
    enriched: list[dict[str, object]] = []
    for projection in projections:
        item = dict(projection)
        try:
            run_id = UUID(str(item["run_id"]))
            run = await get_run(run_id)
            artifacts = await list_artifacts(run_id)
        except (KeyError, TypeError, ValueError):
            enriched.append(_redacted_dict(item))
            continue
        item["status"] = run.status.value
        item["result_text"] = run.result_text
        item["artifacts"] = [
            _redacted_dict(artifact.model_dump(mode="json")) for artifact in artifacts
        ]
        enriched.append(_redacted_dict(item))
    return enriched


app = create_app()
