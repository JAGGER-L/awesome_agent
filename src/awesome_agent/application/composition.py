from __future__ import annotations

import asyncio
import logging
import subprocess
from collections.abc import Awaitable, Callable, Coroutine, Iterator, Mapping
from contextlib import AsyncExitStack, contextmanager, suppress
from contextvars import Context, ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, cast
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import JsonValue

from awesome_agent.agent import (
    AgentRuntimeContext,
    DisabledPostAnswerFinalizer,
    PostAnswerFinalizer,
    TurnBudget,
    compile_agent_graph,
)
from awesome_agent.application.change_commands import ChangeCommandService
from awesome_agent.application.change_scope import ChangeScope
from awesome_agent.application.command_results import CommandOutcome, error
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.context import ApplicationContextService
from awesome_agent.application.contracts import (
    ApplicationResult,
    ApplicationState,
    CancelResult,
    ChangeSetSummary,
    InitializeResult,
    InitializeStatus,
    InteractionResult,
    OperationAccepted,
    ProductError,
    ProductErrorCode,
    ProviderCredentialSetRequest,
    ProviderCredentialSetResult,
    StatusSnapshot,
    ThreadListQuery,
    ThreadListResult,
    ThreadReadQuery,
    ThreadReadResult,
    WorkspacePresentation,
    thread_display_id,
)
from awesome_agent.application.conversation_commands import ConversationCommandService
from awesome_agent.application.diagnostic_commands import DiagnosticCommandService
from awesome_agent.application.diagnostics import ApplicationDiagnosticWriter
from awesome_agent.application.direct import DirectCommandService
from awesome_agent.application.dispatcher import CommandDispatcher
from awesome_agent.application.errors import ApplicationFailure
from awesome_agent.application.events import ApplicationEventProjector
from awesome_agent.application.extension_commands import ApplicationExtensionService
from awesome_agent.application.facade import LocalApplication
from awesome_agent.application.foreground import ForegroundArbiter, ForegroundBusy
from awesome_agent.application.interactions import (
    InteractionCoordinator,
    InteractionDecision,
    InteractionKind,
    PendingInteraction,
    recovery_decision_choices,
    state_reset_choices,
    tool_approval_choices,
    workspace_trust_choices,
)
from awesome_agent.application.middleware import ObservationalMiddleware
from awesome_agent.application.operations import (
    OperationBusy,
    OperationContinuation,
    OperationController,
)
from awesome_agent.application.permission_commands import PermissionCommandService
from awesome_agent.application.provider_configuration import (
    CredentialValidator,
    ProviderConfigurationPublication,
    ProviderConfigurationRecoveryRequired,
    ProviderConfigurationService,
    ProviderConfigurationSnapshot,
    reconcile_provider_credential_transaction,
    reconcile_provider_model_transaction,
)
from awesome_agent.application.runtime_resources import RuntimeResources
from awesome_agent.application.turns import (
    RecoveryResult,
    RecoveryStatus,
    TurnCoordinator,
    TurnExecutionFailed,
)
from awesome_agent.config import (
    ApplicationConfig,
    LoadedConfigSources,
    ProviderCredentialTransactionJournal,
    SecretStatus,
    SecretValues,
    ThreadConfigState,
    TurnConfig,
    UserConfigDocument,
    UserConfigWriter,
    UserSecretStore,
    load_config_sources,
    missing_provider_credential_statuses,
    resolve_application_config,
    resolve_turn_config,
)
from awesome_agent.config.model_transaction import ProviderModelTransactionJournal
from awesome_agent.config.resource_lock import (
    ResourceLockTimeout,
    ResourceLockUnavailable,
)
from awesome_agent.context import (
    CODING_AGENT_PRODUCT_INSTRUCTIONS,
    ContextBuilder,
    ContextManifestItem,
    Mem0ContextResult,
    ThreadCompressor,
    WorkspaceInstructionSnapshot,
    calculate_context_budget,
    estimate_messages,
    load_workspace_instructions,
    mem0_context_source,
)
from awesome_agent.conversation import (
    ConversationService,
    Thread,
    ThreadNotFound,
    ToolActivity,
    ToolActivityOrigin,
    Turn,
    TurnBusy,
    TurnNotFound,
    TurnStatus,
    UsageSummary,
)
from awesome_agent.core.cancellation import (
    finish_bounded_cancellation_cleanup,
    finish_cancellation_safe,
)
from awesome_agent.core.changes import (
    ChangeAnalyzer,
    ChangeJournal,
    ChangeLifecycle,
    ChangeOperations,
    merge_file_changes,
)
from awesome_agent.core.changes.errors import ChangeLifecycleError
from awesome_agent.core.contracts import new_identifier
from awesome_agent.core.events import (
    EventEmitter,
    EventSink,
    InteractionChoicePayload,
    InteractionRequiredPayload,
    InteractionResolvedPayload,
)
from awesome_agent.core.tools import (
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolExecutor,
    ToolRequest,
)
from awesome_agent.core.tools.builtins import (
    register_modifying_tools,
    register_read_tools,
)
from awesome_agent.core.tools.permissions import (
    PermissionMode,
    PermissionSession,
    ToolApprovalDecision,
    ToolApprovalRequest,
)
from awesome_agent.core.tools.process import ProcessRunner
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import (
    TrustStatus,
    WorkspaceIdentity,
    WorkspaceIdentityChanged,
    WorkspaceTrustService,
    require_workspace_identity,
    resolve_workspace,
    workspace_runtime_key,
)
from awesome_agent.extensions.mcp import (
    McpConnectionState,
    McpManager,
    McpServerConfig,
    McpSource,
)
from awesome_agent.extensions.mcp.manager import McpClient
from awesome_agent.extensions.skills import (
    SkillLoader,
    discover_skills,
    register_skill_tools,
)
from awesome_agent.memory import (
    LocalMemoryService,
    Mem0CloudAdapter,
    Mem0CloudError,
    Mem0Diagnostic,
    Mem0Identity,
    Mem0PostAnswerFinalizer,
    MemoryDistiller,
    managed_mem0_client,
    refresh_local_memory_tools,
)
from awesome_agent.memory.mem0_cloud import Mem0Client
from awesome_agent.modeling import (
    GatewayFactory,
    ModelCatalog,
    ModelIdentitySnapshot,
    ProviderId,
)
from awesome_agent.paths import AwesomePaths
from awesome_agent.providers import (
    ProviderCredentialValidator,
    managed_gateway_factory,
)
from awesome_agent.storage import (
    ApplicationMigrationError,
    ApplicationSchemaMismatch,
    ApplicationSQLite,
    ApplicationStateUnavailable,
    ApplicationStateUnknown,
    SQLiteMcpEnablementStore,
    StateCompatibility,
    StateLease,
    StateLeaseMode,
    StateLeaseUnavailable,
    StatePreflight,
    StateResetError,
)
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore
from awesome_agent.storage.checkpoints import (
    LangGraphCheckpointStore,
    sqlite_checkpoint_saver,
)
from awesome_agent.storage.conversations import SQLiteConversationRepositories
from awesome_agent.storage.pagination import (
    InvalidThreadCursor,
    decode_thread_cursor,
    encode_thread_cursor,
)
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore
from awesome_agent.version import PRODUCT_VERSION

type McpClientFactory = Callable[[McpServerConfig], McpClient]

logger = logging.getLogger(__name__)

_MAX_THREAD_RESULT_BYTES = 900_000
_APPLICATION_SHUTDOWN_CLEANUP_TIMEOUT_SECONDS = 45.0
_RECOVERY_EVENT_DELIVERY_ATTEMPTS = 2
_RECOVERY_EVENT_DELIVERY_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class _RecoveryResolutionDelivery:
    interaction_id: str
    decision: InteractionDecision
    thread_id: str
    turn_id: str
    operation_id: str | None = None
    client_message_id: str | None = None


_CAPABILITIES = (
    "threads",
    "turns",
    "direct_commands",
    "commands",
    "tools",
    "skills",
    "mcp",
    "local_memory",
    "mem0_cloud",
)


async def compose_local_application(
    *,
    home: Path,
    workspace: Path,
    event_sink: EventSink,
    environ: Mapping[str, str] | None = None,
    gateway_factory: GatewayFactory | None = None,
    mcp_client_factory: McpClientFactory | None = None,
    mem0_client: object | None = None,
    credential_validator: CredentialValidator | None = None,
) -> LocalApplication:
    paths = AwesomePaths.from_home(home)
    identity = resolve_workspace(workspace)
    session_id = new_identifier("session")
    diagnostics: ApplicationDiagnosticWriter | None = None
    try:
        diagnostics = ApplicationDiagnosticWriter(paths.logs_dir)
    except BaseException:
        diagnostics = None
    stack = AsyncExitStack()
    try:
        backend = _LocalApplicationBackend(
            paths=paths,
            workspace=identity,
            session_id=session_id,
            event_sink=event_sink,
            resources=stack,
            environ=environ,
            gateway_factory=gateway_factory,
            mcp_client_factory=mcp_client_factory,
            mem0_client=mem0_client,
            credential_validator=credential_validator,
        )
        middleware = (
            (
                ObservationalMiddleware(
                    session_id=session_id,
                    correlation_id=lambda: f"correlation_{uuid4().hex}",
                    monotonic=monotonic,
                    sink=diagnostics.try_emit,
                ),
            )
            if diagnostics is not None
            else ()
        )
        application = LocalApplication(
            backend,
            middleware=middleware,
            diagnostics_close=(diagnostics.aclose if diagnostics is not None else None),
        )
    except BaseException:
        try:
            await stack.aclose()
        finally:
            if diagnostics is not None:
                with suppress(BaseException):
                    await diagnostics.aclose()
        raise
    return application


class _Mem0Session:
    def __init__(
        self,
        *,
        enabled: bool,
        adapter: Mem0CloudAdapter | None,
        identity: Mem0Identity | None,
        diagnostic: Mem0Diagnostic | None,
    ) -> None:
        self.enabled = enabled
        self.adapter = adapter
        self.identity = identity
        self.diagnostic = diagnostic

    def update(self, enabled: bool, identity: Mem0Identity | None) -> None:
        self.enabled = enabled
        if identity is not None:
            self.identity = identity

    async def recall(
        self,
        query: str,
        higher_priority_contents: tuple[str, ...],
    ) -> Mem0ContextResult:
        if self.identity is None:
            return (
                Mem0ContextResult(diagnostic=self.diagnostic)
                if self.enabled
                else Mem0ContextResult()
            )
        return await mem0_context_source(
            enabled=self.enabled,
            adapter=self.adapter,
            identity=self.identity,
            query=query,
            higher_priority_contents=higher_priority_contents,
            initialization_diagnostic=self.diagnostic,
        )


@dataclass(frozen=True, slots=True)
class WorkspaceRuntime:
    """One immutable snapshot of the activated workspace service graph."""

    sources: LoadedConfigSources
    application_config: ApplicationConfig
    conversation: ConversationService
    turns: TurnCoordinator
    commands: ConversationCommandService
    command_dispatcher: CommandDispatcher
    diagnostic_commands: DiagnosticCommandService
    change_commands: ChangeCommandService
    permission_commands: PermissionCommandService
    provider_configuration: ProviderConfigurationService
    direct: DirectCommandService
    extensions: ApplicationExtensionService
    context: ApplicationContextService
    tool_registry: ToolRegistry
    model_catalog: ModelCatalog
    local_memory: LocalMemoryService
    mem0_session: _Mem0Session
    mcp: McpManager
    change_scope: ChangeScope
    change_store: SQLiteChangeSetStore
    change_analyzer: ChangeAnalyzer
    change_operations: ChangeOperations
    workspace_branch: str | None
    workspace_instruction_snapshot: WorkspaceInstructionSnapshot
    resources: RuntimeResources


class _LocalApplicationBackend:
    def __init__(
        self,
        *,
        paths: AwesomePaths,
        workspace: WorkspaceIdentity,
        session_id: str,
        event_sink: EventSink,
        resources: AsyncExitStack,
        environ: Mapping[str, str] | None,
        gateway_factory: GatewayFactory | None,
        mcp_client_factory: McpClientFactory | None,
        mem0_client: object | None,
        credential_validator: CredentialValidator | None,
    ) -> None:
        self._paths = paths
        self._workspace = workspace
        self._process_resources = resources
        self._environ = dict(environ or {})
        self._injected_gateway_factory = gateway_factory
        self._mcp_client_factory = mcp_client_factory
        self._injected_mem0_client = mem0_client
        self._credential_validator = (
            credential_validator or ProviderCredentialValidator()
        )
        self._session_id = session_id
        self._emitter = EventEmitter(
            session_id=self._session_id,
            workspace_key=workspace.key,
            sink=event_sink,
        )
        self._foreground = ForegroundArbiter()
        self._interactions = InteractionCoordinator()
        self._operations = OperationController(
            self._emitter,
            self._foreground,
            admission_gate=self._operation_admitted,
        )
        self._state_lease: StateLease | None = None
        self._workspace_path_lease: StateLease | None = None
        self._workspace_entity_lease: StateLease | None = None
        self._process_resources.callback(self._close_state_lease)
        self._process_resources.callback(self._close_workspace_leases)
        self._database = ApplicationSQLite(paths.application_db)
        self._process_resources.push_async_callback(self._database.aclose)
        self._trust = WorkspaceTrustService(SQLiteWorkspaceTrustStore(self._database))
        self._repositories = SQLiteConversationRepositories(self._database)
        self._clock = lambda: datetime.now(UTC)
        self._conversation = ConversationService(
            store=self._repositories,
            clock=self._clock,
        )
        self._provider_model_journal = ProviderModelTransactionJournal(
            paths.provider_model_transaction_file
        )
        self._provider_credential_journal = ProviderCredentialTransactionJournal(
            paths.provider_credential_transaction_file,
            paths.provider_credential_backup_file,
        )
        self._provider_credential_reconciled = False
        self._saver: BaseCheckpointSaver[str] | None = None
        self._checkpoints: LangGraphCheckpointStore | None = None
        self._bootstrap_sources = LoadedConfigSources(
            user=UserConfigDocument(),
            workspace=None,
            secrets=SecretValues(),
            secret_status=SecretStatus(),
            provider_credentials=missing_provider_credential_statuses(),
        )
        self._bootstrap_application_config = resolve_application_config(
            self._bootstrap_sources
        )
        self._runtime: WorkspaceRuntime | None = None
        self._request_runtime = ContextVar[WorkspaceRuntime | None](
            f"awesome_workspace_runtime_{id(self)}",
            default=None,
        )
        self._runtime_retirements: dict[RuntimeResources, asyncio.Task[None]] = {}
        self._shutdown_task: asyncio.Task[None] | None = None
        self._closed = False
        self._recovery_queue: list[RecoveryResult] = []
        self._recovery_resolution_delivery: _RecoveryResolutionDelivery | None = None
        self._recovery_required_delivery_id: str | None = None
        self._recovery_required_delivery_lock = asyncio.Lock()
        self._recovery_event_deliveries: set[asyncio.Task[None]] = set()
        self._permission_session = PermissionSession()
        self._bootstrap_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()

    async def initialize_application(self) -> InitializeResult:
        async with self._bootstrap_lock:
            if self._closed or self._foreground.closing:
                raise _application_failure(
                    ProductErrorCode.INTERNAL_ERROR,
                    "Application is shutting down.",
                )
            return await self._initialize_application_locked()

    async def _initialize_application_locked(self) -> InitializeResult:
        if self._runtime is not None:
            await self._flush_recovery_notifications()
            return InitializeResult(
                product_version=PRODUCT_VERSION,
                protocol_version=3,
                status=InitializeStatus.READY,
                session_id=self._session_id,
                workspace=self._workspace_presentation(include_branch=True),
                capabilities=_CAPABILITIES,
            )
        await self._reconcile_provider_credentials_before_state()
        try:
            await self._ensure_state_lease()
            trust_status = await self._trust.status(self._workspace)
        except ApplicationSchemaMismatch as error:
            if error.direction is StateCompatibility.MIGRATION_UNAVAILABLE:
                return await self._state_reset_required()
            if error.direction is StateCompatibility.NEWER:
                raise self._newer_state_failure(error.found, error.expected) from error
            raise _application_failure(
                ProductErrorCode.STATE_UNAVAILABLE,
                "Awesome could not migrate local state safely.",
                retryable=True,
                data={"state_directory": str(self._paths.state_dir.resolve())},
            ) from error
        except ApplicationMigrationError as error:
            raise _application_failure(
                ProductErrorCode.STATE_UNAVAILABLE,
                "Awesome could not migrate local state safely.",
                retryable=True,
                data={"state_directory": str(self._paths.state_dir.resolve())},
            ) from error
        except ApplicationStateUnknown as error:
            raise _application_failure(
                ProductErrorCode.STATE_UNKNOWN,
                "Awesome cannot identify the local state format.",
                data={"state_directory": str(self._paths.state_dir.resolve())},
            ) from error
        except ApplicationStateUnavailable as error:
            raise _application_failure(
                ProductErrorCode.STATE_UNAVAILABLE,
                "Awesome cannot read local state.",
                retryable=True,
                data={"state_directory": str(self._paths.state_dir.resolve())},
            ) from error
        except StateLeaseUnavailable as error:
            raise _application_failure(
                ProductErrorCode.STATE_UNAVAILABLE,
                "Local state is currently in use.",
                retryable=True,
                data={"state_directory": str(self._paths.state_dir.resolve())},
            ) from error
        if trust_status is not TrustStatus.TRUSTED:
            pending = self._interactions.pending
            if pending is None:
                pending = self._interactions.create(
                    kind=InteractionKind.WORKSPACE_TRUST,
                    prompt="Trust this workspace?",
                    operation="trust",
                    target=str(self._workspace.display_path),
                    capability=None,
                    choices=workspace_trust_choices(),
                )
                await self._emitter.emit(
                    InteractionRequiredPayload(
                        interaction_id=pending.id,
                        interaction_kind="workspace_trust",
                        prompt=pending.prompt,
                        operation=pending.operation,
                        target=pending.target,
                        capability=pending.capability,
                        choices=tuple(
                            InteractionChoicePayload(
                                decision=item.decision.value,
                                label=item.label,
                                description=item.description,
                            )
                            for item in pending.choices
                        ),
                    )
                )
            return InitializeResult(
                product_version=PRODUCT_VERSION,
                protocol_version=3,
                status=InitializeStatus.TRUST_REQUIRED,
                session_id=self._session_id,
                interaction_id=pending.id,
                workspace=self._workspace_presentation(include_branch=False),
                capabilities=_CAPABILITIES,
            )
        await self._activate_workspace()
        return InitializeResult(
            product_version=PRODUCT_VERSION,
            protocol_version=3,
            status=InitializeStatus.READY,
            session_id=self._session_id,
            workspace=self._workspace_presentation(include_branch=True),
            capabilities=_CAPABILITIES,
        )

    async def _state_reset_required(self) -> InitializeResult:
        pending = self._interactions.pending
        if pending is None:
            pending = self._interactions.create(
                kind=InteractionKind.STATE_RESET,
                prompt="Awesome needs to reset local state",
                operation="reset_local_state",
                target="local state",
                capability=None,
                choices=state_reset_choices(),
            )
            await self._emitter.emit(
                InteractionRequiredPayload(
                    interaction_id=pending.id,
                    interaction_kind="state_reset",
                    prompt=pending.prompt,
                    operation=pending.operation,
                    target=pending.target,
                    capability=pending.capability,
                    choices=tuple(
                        InteractionChoicePayload(
                            decision=item.decision.value,
                            label=item.label,
                            description=item.description,
                        )
                        for item in pending.choices
                    ),
                )
            )
        elif pending.kind is not InteractionKind.STATE_RESET:
            raise _application_failure(
                ProductErrorCode.INTERNAL_ERROR,
                "Another startup interaction is already active.",
            )
        return InitializeResult(
            product_version=PRODUCT_VERSION,
            protocol_version=3,
            status=InitializeStatus.STATE_RESET_REQUIRED,
            session_id=self._session_id,
            interaction_id=pending.id,
            workspace=self._workspace_presentation(include_branch=False),
            capabilities=_CAPABILITIES,
        )

    def _newer_state_failure(
        self,
        found_schema: int,
        expected_schema: int,
    ) -> ApplicationFailure:
        return _application_failure(
            ProductErrorCode.STATE_CREATED_BY_NEWER_VERSION,
            (
                "Local state was created by a newer Awesome version. "
                "Upgrade Awesome to continue."
            ),
            data={
                "found_schema": found_schema,
                "expected_schema": expected_schema,
                "state_directory": str(self._paths.state_dir.resolve()),
            },
        )

    async def application_state(self) -> ApplicationState:
        self._require_open()
        runtime = self._request_runtime.get() or self._runtime
        if runtime is not None:
            with self._runtime_request_scope(runtime) as bound_runtime:
                return await self._application_state_in_runtime(bound_runtime)
        return await self._application_state_in_runtime(None)

    async def _application_state_in_runtime(
        self,
        runtime: WorkspaceRuntime | None,
    ) -> ApplicationState:
        conversation = (
            runtime.conversation if runtime is not None else self._conversation
        )
        sources = runtime.sources if runtime is not None else self._bootstrap_sources
        application_config = (
            runtime.application_config
            if runtime is not None
            else self._bootstrap_application_config
        )
        current_id = runtime.commands.current_thread_id if runtime is not None else None
        current_view = (
            await conversation.read_thread(current_id)
            if current_id is not None
            else None
        )
        current = current_view.thread if current_view is not None else None
        usage = UsageSummary()
        if current_view is not None:
            for turn in current_view.turns:
                usage += turn.usage
        local_enabled = runtime.local_memory.enabled if runtime is not None else False
        mem0_session = runtime.mem0_session if runtime is not None else None
        return ApplicationState(
            initialized=runtime is not None,
            session_id=self._session_id,
            workspace_key=self._workspace.key,
            workspace=self._workspace_presentation(
                include_branch=True,
                runtime=runtime,
            ),
            workspace_trusted=runtime is not None,
            current_thread_id=current_id,
            model_identity=(
                self._model_identity(current, runtime=runtime) if current else None
            ),
            thinking_enabled=current.thinking_enabled if current else True,
            skill_mode=current.skill_mode if current else "auto",
            active_operation_id=self._operations.active_operation_id,
            pending_interaction_id=(
                self._interactions.pending.id if self._interactions.pending else None
            ),
            permission_mode=self._permission_session.mode,
            configuration_valid=True,
            secret_status=sources.secret_status,
            provider_credentials=sources.provider_credentials,
            memory_status={
                "local": {"enabled": local_enabled},
                "mem0": {
                    "enabled": (
                        mem0_session.enabled
                        if mem0_session is not None
                        else application_config.memory.mem0_cloud
                    ),
                    "available": (
                        mem0_session.adapter is not None
                        if mem0_session is not None
                        else False
                    ),
                },
            },
            mcp_status=tuple(
                {
                    "server_id": status.server_id,
                    "state": status.state.value,
                }
                for status in (runtime.mcp.statuses() if runtime is not None else ())
            ),
            usage=usage.model_dump(mode="json"),
            configuration_diagnostics=(
                (mem0_session.diagnostic.code,)
                if (
                    mem0_session is not None
                    and mem0_session.diagnostic is not None
                    and mem0_session.enabled
                )
                else ()
            ),
            workspace_instruction_diagnostic=(
                runtime.workspace_instruction_snapshot.diagnostic
                if runtime is not None
                else None
            ),
        )

    async def workspace_threads(self, query: ThreadListQuery) -> ThreadListResult:
        runtime = self._require_runtime()
        with self._runtime_request_scope(runtime) as bound_runtime:
            return await self._workspace_threads_in_runtime(
                query, runtime=bound_runtime
            )

    async def _workspace_threads_in_runtime(
        self,
        query: ThreadListQuery,
        *,
        runtime: WorkspaceRuntime,
    ) -> ThreadListResult:
        try:
            cursor = (
                decode_thread_cursor(query.cursor) if query.cursor is not None else None
            )
        except InvalidThreadCursor as error:
            raise _application_failure(
                ProductErrorCode.INVALID_ARGUMENTS,
                "Thread cursor is invalid.",
            ) from error
        page = await runtime.conversation.list_thread_page(
            self._workspace.key,
            cursor=cursor,
            limit=query.limit,
        )
        next_cursor = (
            encode_thread_cursor((page.threads[-1].updated_at, page.threads[-1].id))
            if page.has_more and page.threads
            else None
        )
        return ThreadListResult(
            threads=page.threads,
            has_more=page.has_more,
            next_cursor=next_cursor,
        )

    async def thread_state(self, query: ThreadReadQuery) -> ThreadReadResult:
        runtime = self._require_runtime()
        with self._runtime_request_scope(runtime) as bound_runtime:
            return await self._thread_state_in_runtime(query, runtime=bound_runtime)

    async def _thread_state_in_runtime(
        self,
        query: ThreadReadQuery,
        *,
        runtime: WorkspaceRuntime,
    ) -> ThreadReadResult:
        limit = query.limit
        while True:
            try:
                page = await runtime.conversation.read_thread_page(
                    query.thread_id,
                    before_sequence=query.before_sequence,
                    limit=limit,
                )
            except ThreadNotFound as error:
                raise _application_failure(
                    ProductErrorCode.THREAD_NOT_FOUND,
                    "Thread was not found.",
                ) from error
            if page.view.thread.workspace_key != self._workspace.key:
                raise _application_failure(
                    ProductErrorCode.THREAD_NOT_FOUND,
                    "Thread was not found.",
                )
            result = ThreadReadResult(
                view=page.view,
                change_sets=await self._page_change_summaries(
                    page.view.tool_activities,
                    runtime=runtime,
                ),
                has_more=page.has_more,
                next_before_sequence=page.next_before_sequence,
            )
            encoded = (
                ApplicationResult.success(result).model_dump_json().encode("utf-8")
            )
            if len(encoded) <= _MAX_THREAD_RESULT_BYTES:
                return result
            if limit == 1:
                raise _application_failure(
                    ProductErrorCode.INTERNAL_ERROR,
                    "Thread entry exceeds the protocol response limit.",
                )
            limit = max(1, limit // 2)

    async def start_turn(
        self,
        thread_id: str,
        content: str,
        client_message_id: str,
    ) -> OperationAccepted:
        runtime = self._require_runtime()
        with self._runtime_request_scope(runtime):
            return await self._start_turn_in_runtime(
                runtime,
                thread_id,
                content,
                client_message_id,
            )

    async def _start_turn_in_runtime(
        self,
        runtime: WorkspaceRuntime,
        thread_id: str,
        content: str,
        client_message_id: str,
    ) -> OperationAccepted:
        self._require_selected_thread(thread_id, runtime=runtime)
        if not content.strip():
            raise _application_failure(
                ProductErrorCode.INVALID_ARGUMENTS,
                "Turn input is invalid.",
            )
        if self._interactions.pending is not None:
            raise _application_failure(
                ProductErrorCode.OPERATION_BUSY,
                "Resolve the pending interaction before starting a Turn.",
                retryable=True,
            )
        try:
            await self._require_runtime_consistent(runtime)
            thread = (await runtime.conversation.read_thread(thread_id)).thread
            config = self._turn_config(thread, runtime=runtime)
            self._require_provider_configured(config.provider, runtime=runtime)
            return await runtime.turns.submit_turn(
                thread_id,
                content,
                client_message_id=client_message_id,
            )
        except ProviderConfigurationRecoveryRequired as error:
            raise _application_failure(
                ProductErrorCode.RECOVERY_REQUIRED,
                "Provider configuration recovery is required. Restart Awesome.",
                retryable=False,
            ) from error
        except ApplicationFailure:
            raise
        except OperationBusy as error:
            raise _application_failure(
                ProductErrorCode.OPERATION_BUSY,
                "Another operation is active.",
                retryable=True,
            ) from error
        except TurnBusy as error:
            raise _application_failure(
                ProductErrorCode.TURN_BUSY,
                "The Thread already has an active Turn.",
                retryable=True,
            ) from error
        except ThreadNotFound as error:
            raise _application_failure(
                ProductErrorCode.THREAD_NOT_FOUND,
                "Thread was not found.",
            ) from error
        except TurnNotFound as error:
            raise _application_failure(
                ProductErrorCode.TURN_NOT_FOUND,
                "Turn was not found.",
            ) from error

    async def start_direct(self, thread_id: str, command: str) -> OperationAccepted:
        runtime = self._require_runtime()
        with self._runtime_request_scope(runtime):
            return await self._start_direct_in_runtime(runtime, thread_id, command)

    async def _start_direct_in_runtime(
        self,
        runtime: WorkspaceRuntime,
        thread_id: str,
        command: str,
    ) -> OperationAccepted:
        self._require_selected_thread(thread_id, runtime=runtime)
        if not command.strip():
            raise _application_failure(
                ProductErrorCode.INVALID_ARGUMENTS,
                "Direct command is invalid.",
            )
        if self._interactions.pending is not None:
            raise _application_failure(
                ProductErrorCode.OPERATION_BUSY,
                "Resolve the pending interaction before running a command.",
                retryable=True,
            )
        try:
            await self._require_runtime_consistent(runtime)
            return await runtime.direct.start(thread_id, command)
        except ApplicationFailure:
            raise
        except OperationBusy as error:
            raise _application_failure(
                ProductErrorCode.OPERATION_BUSY,
                "Another operation is active.",
                retryable=True,
            ) from error
        except ThreadNotFound as error:
            raise _application_failure(
                ProductErrorCode.THREAD_NOT_FOUND,
                "Thread was not found.",
            ) from error

    async def run_command(self, intent: CommandIntent) -> CommandOutcome:
        runtime = self._require_runtime()
        with self._runtime_request_scope(runtime):
            try:
                return await runtime.command_dispatcher.dispatch(intent)
            except ProviderConfigurationRecoveryRequired as error:
                raise _application_failure(
                    ProductErrorCode.RECOVERY_REQUIRED,
                    "Provider configuration recovery is required. Restart Awesome.",
                    retryable=False,
                ) from error
            except ThreadNotFound as error:
                raise _application_failure(
                    ProductErrorCode.THREAD_NOT_FOUND,
                    "Thread was not found.",
                ) from error

    async def set_provider_credential(
        self,
        request: ProviderCredentialSetRequest,
    ) -> ProviderCredentialSetResult:
        runtime = self._require_runtime()
        with self._runtime_request_scope(runtime):
            return await self._set_provider_credential_in_runtime(runtime, request)

    async def _set_provider_credential_in_runtime(
        self,
        runtime: WorkspaceRuntime,
        request: ProviderCredentialSetRequest,
    ) -> ProviderCredentialSetResult:
        if self._interactions.pending is not None:
            raise _application_failure(
                ProductErrorCode.OPERATION_BUSY,
                "Resolve the pending interaction before changing credentials.",
                retryable=True,
            )
        try:
            lease = self._foreground.acquire_exclusive()
        except ForegroundBusy as error:
            raise _application_failure(
                ProductErrorCode.OPERATION_BUSY,
                "Another foreground operation is active.",
                retryable=True,
            ) from error
        async with lease:
            try:
                await self._require_runtime_consistent(runtime)
                return await runtime.provider_configuration.set_credential(request)
            except ApplicationFailure:
                raise
            except ProviderConfigurationRecoveryRequired as error:
                raise _application_failure(
                    ProductErrorCode.RECOVERY_REQUIRED,
                    "Provider configuration recovery is required. Restart Awesome.",
                    retryable=False,
                ) from error
            except ResourceLockTimeout as error:
                raise _application_failure(
                    ProductErrorCode.OPERATION_BUSY,
                    "User state is being changed by another Awesome process.",
                    retryable=True,
                ) from error
            except ResourceLockUnavailable as error:
                raise _application_failure(
                    ProductErrorCode.STATE_UNAVAILABLE,
                    "User state cannot be accessed safely.",
                    retryable=True,
                    data={"state_directory": str(self._paths.state_dir.resolve())},
                ) from error

    async def resolve_interaction(
        self,
        interaction_id: str,
        decision: str,
    ) -> InteractionResult:
        self._require_open()
        runtime = self._request_runtime.get() or self._runtime
        if runtime is None:
            return await self._resolve_interaction_in_runtime(
                interaction_id,
                decision,
                runtime=None,
            )
        with self._runtime_request_scope(runtime):
            return await self._resolve_interaction_in_runtime(
                interaction_id,
                decision,
                runtime=runtime,
            )

    async def _resolve_interaction_in_runtime(
        self,
        interaction_id: str,
        decision: str,
        *,
        runtime: WorkspaceRuntime | None,
    ) -> InteractionResult:
        if runtime is not None:
            await self._require_runtime_consistent(runtime)
        pending = self._interactions.pending
        if pending is None or pending.id != interaction_id:
            return InteractionResult(accepted=False, status="not_found")
        try:
            parsed = InteractionDecision(decision)
        except ValueError:
            return InteractionResult(accepted=False, status="invalid_decision")
        if pending.kind is InteractionKind.RECOVERY_DECISION:
            if runtime is None:
                raise _application_failure(
                    ProductErrorCode.WORKSPACE_NOT_TRUSTED,
                    "Trust the workspace before resolving recovery.",
                )
            async with self._bootstrap_lock:
                return await self._resolve_recovery_interaction(
                    pending,
                    parsed,
                    runtime=runtime,
                )
        if pending.kind is InteractionKind.STATE_RESET:
            try:
                lease = self._foreground.acquire_interaction_resolution()
            except ForegroundBusy:
                return InteractionResult(accepted=False, status="operation_busy")
            async with lease:
                return await self._resolve_state_reset_interaction(
                    interaction_id,
                    parsed,
                )
        if pending.kind is InteractionKind.WORKSPACE_TRUST:
            try:
                lease = self._foreground.acquire_interaction_resolution()
            except ForegroundBusy:
                return InteractionResult(accepted=False, status="operation_busy")
            async with lease, self._bootstrap_lock:
                current = self._interactions.pending
                if (
                    current is None
                    or current.id != interaction_id
                    or current.kind is not InteractionKind.WORKSPACE_TRUST
                ):
                    return InteractionResult(accepted=False, status="not_found")
                if parsed is InteractionDecision.TRUST:
                    self._prepare_workspace_activation()
                if not self._interactions.resolve(interaction_id, parsed):
                    return InteractionResult(accepted=False, status="rejected")
                resolved = await self._interactions.wait(interaction_id)
                # The event acknowledges the decision. Apply the security upgrade only
                # after successful delivery so a broken client channel fails closed.
                await self._emitter.emit(
                    InteractionResolvedPayload(
                        interaction_id=interaction_id,
                        decision=resolved.value,
                    ),
                )
                if resolved is InteractionDecision.TRUST:
                    await self._trust.accept(self._workspace)
                    await self._activate_workspace()
                return InteractionResult(
                    accepted=True,
                    status=(
                        "resolved"
                        if resolved is InteractionDecision.TRUST
                        else "denied"
                    ),
                )
        if pending.kind is InteractionKind.TOOL_APPROVAL:
            current_thread_id = (
                runtime.commands.current_thread_id if runtime is not None else None
            )
            if (
                pending.operation_id != self._operations.active_operation_id
                or pending.thread_id != current_thread_id
                or pending.thread_id != self._operations.active_thread_id
                or pending.turn_id != self._operations.active_turn_id
            ):
                self._interactions.discard(interaction_id)
                return InteractionResult(accepted=False, status="stale")
            if not self._interactions.resolve(interaction_id, parsed):
                return InteractionResult(accepted=False, status="rejected")
            await self._emitter.emit(
                InteractionResolvedPayload(
                    interaction_id=interaction_id,
                    decision=parsed.value,
                ),
                thread_id=pending.thread_id,
                turn_id=pending.turn_id,
                operation_id=pending.operation_id,
            )
            return InteractionResult(accepted=True, status="resolved")
        if pending.kind is InteractionKind.FULL_ACCESS_CONFIRMATION:
            try:
                lease = self._foreground.acquire_interaction_resolution()
            except ForegroundBusy:
                return InteractionResult(accepted=False, status="operation_busy")
            async with lease:
                current = self._interactions.pending
                current_thread_id = (
                    runtime.commands.current_thread_id if runtime is not None else None
                )
                if (
                    current is None
                    or current.id != interaction_id
                    or current.thread_id != current_thread_id
                    or current.permission_generation
                    != self._permission_session.generation
                ):
                    self._interactions.discard(interaction_id)
                    return InteractionResult(accepted=False, status="stale")
                if not self._interactions.resolve(interaction_id, parsed):
                    return InteractionResult(accepted=False, status="rejected")
                resolved = await self._interactions.wait(interaction_id)
                await self._emitter.emit(
                    InteractionResolvedPayload(
                        interaction_id=interaction_id,
                        decision=resolved.value,
                    ),
                    thread_id=current_thread_id,
                )
                if resolved is InteractionDecision.ENABLE_FULL_ACCESS:
                    self._permission_session.set_mode(PermissionMode.FULL_ACCESS)
                return InteractionResult(
                    accepted=True,
                    status=(
                        "resolved"
                        if resolved is InteractionDecision.ENABLE_FULL_ACCESS
                        else "denied"
                    ),
                )
        return InteractionResult(accepted=False, status="rejected")

    async def _resolve_recovery_interaction(
        self,
        pending: PendingInteraction,
        decision: InteractionDecision,
        *,
        runtime: WorkspaceRuntime,
    ) -> InteractionResult:
        if decision is InteractionDecision.RETRY:
            return await self._retry_recovery(pending, runtime=runtime)
        if decision is not InteractionDecision.ABORT:
            return InteractionResult(accepted=False, status="rejected")
        try:
            lease = self._foreground.acquire_interaction_resolution()
        except ForegroundBusy:
            return InteractionResult(accepted=False, status="operation_busy")
        async with lease:
            recovery = self._bound_recovery(pending)
            if recovery is None:
                self._interactions.discard(pending.id)
                return InteractionResult(accepted=False, status="stale")
            try:
                await runtime.turns.abort_recovery(
                    recovery.thread_id,
                    recovery.turn_id,
                )
            except TurnExecutionFailed:
                self._discard_recovery(pending, recovery)
                await self._present_next_recovery()
                return InteractionResult(accepted=False, status="stale")
            self._discard_recovery(pending, recovery)
            delivery = _RecoveryResolutionDelivery(
                interaction_id=pending.id,
                decision=InteractionDecision.ABORT,
                thread_id=recovery.thread_id,
                turn_id=recovery.turn_id,
            )
            self._recovery_resolution_delivery = delivery
            (
                delivered,
                cancellation,
                failure,
            ) = await self._flush_recovery_resolution_delivery()
            if delivered:
                await self._present_next_recovery()
            if cancellation is not None:
                raise cancellation
            if failure is not None:
                raise failure
            if not delivered:
                raise RuntimeError("Recovery resolution delivery did not complete.")
            return InteractionResult(accepted=True, status="resolved")

    async def _retry_recovery(
        self,
        pending: PendingInteraction,
        *,
        runtime: WorkspaceRuntime,
    ) -> InteractionResult:
        recovery = self._bound_recovery(pending)
        if recovery is None:
            self._interactions.discard(pending.id)
            return InteractionResult(accepted=False, status="stale")
        turns = runtime.turns
        commands = runtime.commands
        claimed = False
        resolution_published = asyncio.Event()

        async def claim(turn: Turn) -> None:
            nonlocal claimed
            current = self._bound_recovery(pending)
            if current != recovery or turn.id != recovery.turn_id:
                raise TurnExecutionFailed("recovery_stale")
            await commands.select_recovery_thread(recovery.thread_id)
            if not self._interactions.discard(pending.id):
                raise TurnExecutionFailed("recovery_stale")
            if self._recovery_required_delivery_id == pending.id:
                self._recovery_required_delivery_id = None
            self._recovery_queue.pop(0)
            claimed = True

        async def finished() -> None:
            await resolution_published.wait()
            if self._recovery_resolution_delivery is None:
                await self._present_next_recovery()

        resume_task = asyncio.create_task(
            turns.resume_unfinished(
                recovery.thread_id,
                expected_turn_id=recovery.turn_id,
                continuation=OperationContinuation(
                    interaction_id=pending.id,
                    interaction_generation=pending.generation,
                    thread_id=recovery.thread_id,
                    turn_id=recovery.turn_id,
                ),
                claim=claim,
                finished=finished,
            )
        )

        async def finish_claimed_resume() -> OperationAccepted:
            while not resume_task.done():
                try:
                    await asyncio.shield(resume_task)
                except asyncio.CancelledError:
                    continue
            return resume_task.result()

        response_cancellation: asyncio.CancelledError | None = None
        try:
            try:
                accepted = await asyncio.shield(resume_task)
            except asyncio.CancelledError as cancellation:
                response_cancellation = cancellation
                if claimed:
                    accepted = await finish_claimed_resume()
                else:
                    resume_task.cancel()
                    accepted = await resume_task
        except OperationBusy:
            resolution_published.set()
            if response_cancellation is not None:
                raise response_cancellation from None
            return InteractionResult(accepted=False, status="operation_busy")
        except TurnExecutionFailed:
            resolution_published.set()
            if claimed and await self._recovery_is_in_progress(
                recovery, runtime=runtime
            ):
                self._recovery_queue.insert(0, recovery)
            elif not claimed:
                self._discard_recovery(pending, recovery)
            await self._present_next_recovery()
            if response_cancellation is not None:
                raise response_cancellation from None
            return InteractionResult(accepted=False, status="stale")
        except asyncio.CancelledError:
            resolution_published.set()
            if claimed and await self._recovery_is_in_progress(
                recovery, runtime=runtime
            ):
                self._recovery_queue.insert(0, recovery)
                await self._present_next_recovery()
            if response_cancellation is not None:
                raise response_cancellation from None
            raise
        except BaseException:
            resolution_published.set()
            if claimed and await self._recovery_is_in_progress(
                recovery, runtime=runtime
            ):
                self._recovery_queue.insert(0, recovery)
                await self._present_next_recovery()
            raise

        delivery = _RecoveryResolutionDelivery(
            interaction_id=pending.id,
            decision=InteractionDecision.RETRY,
            thread_id=recovery.thread_id,
            turn_id=recovery.turn_id,
            operation_id=accepted.operation_id,
            client_message_id=accepted.client_message_id,
        )
        self._recovery_resolution_delivery = delivery
        try:
            (
                delivered,
                delivery_cancellation,
                failure,
            ) = await self._flush_recovery_resolution_delivery()
        finally:
            resolution_published.set()
        pending_cancellation = response_cancellation or delivery_cancellation
        if pending_cancellation is not None:
            raise pending_cancellation
        if failure is not None:
            raise failure
        if not delivered:
            raise RuntimeError("Recovery resolution delivery did not complete.")
        return InteractionResult(accepted=True, status="resolved")

    async def _flush_recovery_notifications(self) -> None:
        if self._recovery_resolution_delivery is not None:
            (
                delivered,
                cancellation,
                failure,
            ) = await self._flush_recovery_resolution_delivery()
            if cancellation is not None:
                raise cancellation
            if failure is not None:
                raise failure
            if not delivered:
                return
        await self._present_next_recovery()

    async def _flush_recovery_resolution_delivery(
        self,
    ) -> tuple[bool, asyncio.CancelledError | None, BaseException | None]:
        delivery = self._recovery_resolution_delivery
        if delivery is None:
            return True, None, None

        async def emit() -> None:
            await self._emitter.emit(
                InteractionResolvedPayload(
                    interaction_id=delivery.interaction_id,
                    decision=delivery.decision.value,
                ),
                thread_id=delivery.thread_id,
                turn_id=delivery.turn_id,
                operation_id=delivery.operation_id,
                client_message_id=delivery.client_message_id,
            )

        delivered, cancellation, failure = await self._deliver_recovery_event(emit)
        if delivered and self._recovery_resolution_delivery == delivery:
            self._recovery_resolution_delivery = None
        return delivered, cancellation, failure

    async def _deliver_recovery_event(
        self,
        emit: Callable[[], Coroutine[Any, Any, None]],
    ) -> tuple[bool, asyncio.CancelledError | None, BaseException | None]:
        cancellation: asyncio.CancelledError | None = None
        failure: BaseException | None = None
        for _ in range(_RECOVERY_EVENT_DELIVERY_ATTEMPTS):
            if self._closed or self._foreground.closing:
                break
            delivery_task: asyncio.Task[None] = asyncio.create_task(emit())
            self._recovery_event_deliveries.add(delivery_task)
            delivery_task.add_done_callback(self._recovery_event_deliveries.discard)
            deadline = (
                asyncio.get_running_loop().time()
                + _RECOVERY_EVENT_DELIVERY_TIMEOUT_SECONDS
            )
            while not delivery_task.done():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    delivery_task.cancel()
                    await asyncio.gather(delivery_task, return_exceptions=True)
                    failure = TimeoutError("Recovery event delivery timed out.")
                    break
                try:
                    await asyncio.wait_for(
                        asyncio.shield(delivery_task),
                        timeout=remaining,
                    )
                except asyncio.CancelledError as caught:
                    if cancellation is None:
                        cancellation = caught
                    if self._closed or self._foreground.closing:
                        delivery_task.cancel()
                        await asyncio.gather(delivery_task, return_exceptions=True)
                        return False, cancellation, failure
                    continue
                except TimeoutError:
                    delivery_task.cancel()
                    await asyncio.gather(delivery_task, return_exceptions=True)
                    failure = TimeoutError("Recovery event delivery timed out.")
                    break
                except Exception as caught:
                    failure = caught
                    break
            if delivery_task.done() and not delivery_task.cancelled():
                try:
                    delivery_task.result()
                except Exception as caught:
                    failure = caught
                else:
                    return True, cancellation, None
            if self._closed or self._foreground.closing:
                break
        return False, cancellation, failure

    def _bound_recovery(
        self,
        expected: PendingInteraction,
    ) -> RecoveryResult | None:
        current = self._interactions.pending
        if (
            current is None
            or current.id != expected.id
            or current.kind is not InteractionKind.RECOVERY_DECISION
            or current.generation != expected.generation
            or current.thread_id != expected.thread_id
            or current.turn_id != expected.turn_id
            or current.operation != expected.operation
            or not self._recovery_queue
        ):
            return None
        recovery = self._recovery_queue[0]
        if (
            recovery.thread_id != current.thread_id
            or recovery.turn_id != current.turn_id
        ):
            return None
        return recovery

    def _operation_admitted(
        self,
        continuation: OperationContinuation | None,
    ) -> bool:
        """Bind operation admission to the interaction state under one lease."""

        pending = self._interactions.pending
        if pending is None:
            return continuation is None
        if (
            continuation is None
            or pending.kind is not InteractionKind.RECOVERY_DECISION
        ):
            return False
        return (
            pending.id == continuation.interaction_id
            and pending.generation == continuation.interaction_generation
            and pending.thread_id == continuation.thread_id
            and pending.turn_id == continuation.turn_id
        )

    def _discard_recovery(
        self,
        pending: PendingInteraction,
        recovery: RecoveryResult,
    ) -> None:
        self._interactions.discard(pending.id)
        if self._recovery_required_delivery_id == pending.id:
            self._recovery_required_delivery_id = None
        if self._recovery_queue and self._recovery_queue[0] == recovery:
            self._recovery_queue.pop(0)

    async def _recovery_is_in_progress(
        self,
        recovery: RecoveryResult,
        *,
        runtime: WorkspaceRuntime,
    ) -> bool:
        try:
            view = await runtime.conversation.read_thread(recovery.thread_id)
        except ThreadNotFound:
            return False
        return any(
            turn.id == recovery.turn_id and turn.status is TurnStatus.IN_PROGRESS
            for turn in view.turns
        )

    async def _present_next_recovery(self) -> None:
        async with self._recovery_required_delivery_lock:
            await self._present_next_recovery_locked()

    async def _present_next_recovery_locked(self) -> None:
        if (
            self._closed
            or self._foreground.closing
            or self._recovery_resolution_delivery is not None
            or not self._recovery_queue
        ):
            return
        recovery = self._recovery_queue[0]
        pending = self._interactions.pending
        if pending is None:
            uncertain = recovery.status is RecoveryStatus.INTERACTION_REQUIRED
            pending = self._interactions.create(
                kind=InteractionKind.RECOVERY_DECISION,
                prompt=(
                    "A tool may have produced external side effects. Retry or abort "
                    "this unfinished Turn?"
                    if uncertain
                    else "Resume this unfinished Turn from its verified checkpoint?"
                ),
                operation=(
                    "recover_uncertain_turn" if uncertain else "recover_unfinished_turn"
                ),
                target=(
                    "uncertain external tool call"
                    if uncertain
                    else f"unfinished Turn {recovery.turn_id}"
                ),
                capability=None,
                choices=recovery_decision_choices(uncertain=uncertain),
                thread_id=recovery.thread_id,
                turn_id=recovery.turn_id,
            )
            self._recovery_required_delivery_id = pending.id
        elif (
            pending.kind is not InteractionKind.RECOVERY_DECISION
            or pending.id != self._recovery_required_delivery_id
            or pending.thread_id != recovery.thread_id
            or pending.turn_id != recovery.turn_id
        ):
            return

        async def emit() -> None:
            await self._emitter.emit(
                InteractionRequiredPayload(
                    interaction_id=pending.id,
                    interaction_kind=InteractionKind.RECOVERY_DECISION.value,
                    prompt=pending.prompt,
                    operation=pending.operation,
                    target=pending.target,
                    capability=pending.capability,
                    choices=tuple(
                        InteractionChoicePayload(
                            decision=choice.decision.value,
                            label=choice.label,
                            description=choice.description,
                        )
                        for choice in pending.choices
                    ),
                ),
                thread_id=recovery.thread_id,
                turn_id=recovery.turn_id,
            )

        delivered, cancellation, failure = await self._deliver_recovery_event(emit)
        if delivered and self._recovery_required_delivery_id == pending.id:
            self._recovery_required_delivery_id = None
        if cancellation is not None:
            raise cancellation
        if failure is not None and not delivered:
            logger.warning(
                "Recovery interaction delivery remains pending.",
                exc_info=(type(failure), failure, failure.__traceback__),
            )

    async def _resolve_state_reset_interaction(
        self,
        interaction_id: str,
        decision: InteractionDecision,
    ) -> InteractionResult:
        async with self._bootstrap_lock:
            pending = self._interactions.pending
            if pending is None or pending.id != interaction_id:
                return InteractionResult(accepted=False, status="not_found")
            if not self._interactions.allows(interaction_id, decision):
                return InteractionResult(accepted=False, status="rejected")
            if decision is InteractionDecision.RESET_STATE:
                try:
                    await self._recover_older_state()
                except ApplicationFailure as failure:
                    return InteractionResult(
                        accepted=False,
                        status=failure.error.code.value,
                        error=failure.error,
                    )
            elif decision is not InteractionDecision.DENY:
                return InteractionResult(accepted=False, status="rejected")

            if not self._interactions.resolve(interaction_id, decision):
                return InteractionResult(accepted=False, status="rejected")
            resolved = await self._interactions.wait(interaction_id)
            await self._emitter.emit(
                InteractionResolvedPayload(
                    interaction_id=interaction_id,
                    decision=resolved.value,
                ),
            )
            return InteractionResult(
                accepted=True,
                status=(
                    "denied" if resolved is InteractionDecision.DENY else "resolved"
                ),
            )

    async def _recover_older_state(self) -> None:
        if self._state_lease is not None and self._state_lease.active:
            return
        try:
            exclusive = StateLease.acquire(
                self._paths.home,
                StateLeaseMode.EXCLUSIVE,
            )
        except StateLeaseUnavailable as error:
            raise _application_failure(
                ProductErrorCode.STATE_RESET_BUSY,
                "Close other Awesome sessions before resetting local state.",
                retryable=True,
                data={"state_directory": str(self._paths.state_dir.resolve())},
            ) from error

        cancellation: asyncio.CancelledError | None = None
        try:
            preflight = await self._database.preflight()
            if preflight.compatibility is StateCompatibility.MIGRATION_UNAVAILABLE:
                cancellation = await _finish_state_mutation(
                    self._database.reset(exclusive)
                )
            elif preflight.compatibility is StateCompatibility.MIGRATION_REQUIRED:
                cancellation = await _finish_state_mutation(
                    self._database.migrate(exclusive)
                )
            elif preflight.compatibility in {
                StateCompatibility.NEW,
                StateCompatibility.CURRENT,
            }:
                cancellation = await _finish_state_mutation(self._database.initialize())
            elif preflight.compatibility is StateCompatibility.NEWER:
                assert preflight.found_schema is not None
                raise self._newer_state_failure(
                    preflight.found_schema,
                    preflight.expected_schema,
                )
            elif preflight.compatibility is StateCompatibility.UNKNOWN:
                raise _application_failure(
                    ProductErrorCode.STATE_UNKNOWN,
                    "Awesome cannot identify the local state format.",
                    data={"state_directory": str(self._paths.state_dir.resolve())},
                )
            try:
                exclusive.downgrade()
            except StateLeaseUnavailable:
                await _finish_state_mutation(self._database.suspend())
                raise
            initialize_cancellation = await _finish_state_mutation(
                self._database.initialize()
            )
            if cancellation is None:
                cancellation = initialize_cancellation
        except asyncio.CancelledError:
            exclusive.close()
            raise
        except ApplicationFailure:
            exclusive.close()
            raise
        except ApplicationStateUnavailable as error:
            exclusive.close()
            raise _application_failure(
                ProductErrorCode.STATE_UNAVAILABLE,
                "Awesome cannot read local state.",
                retryable=True,
                data={"state_directory": str(self._paths.state_dir.resolve())},
            ) from error
        except StateLeaseUnavailable as error:
            exclusive.close()
            raise _application_failure(
                ProductErrorCode.STATE_RESET_BUSY,
                "Local state changed while Awesome was resetting it. Try again.",
                retryable=True,
                data={"state_directory": str(self._paths.state_dir.resolve())},
            ) from error
        except StateResetError as error:
            exclusive.close()
            code = (
                ProductErrorCode.STATE_RESET_BUSY
                if error.code == "state_replacement_failed"
                else ProductErrorCode.STATE_RESET_FAILED
            )
            raise _application_failure(
                code,
                (
                    "Close other Awesome sessions before resetting local state."
                    if code is ProductErrorCode.STATE_RESET_BUSY
                    else "Awesome could not reset local state safely."
                ),
                retryable=True,
                data={
                    "diagnostic_code": error.code,
                    "state_directory": str(self._paths.state_dir.resolve()),
                },
            ) from error
        except ApplicationMigrationError as error:
            exclusive.close()
            raise _application_failure(
                ProductErrorCode.STATE_UNAVAILABLE,
                "Awesome could not migrate local state safely.",
                retryable=True,
                data={"state_directory": str(self._paths.state_dir.resolve())},
            ) from error
        self._state_lease = exclusive
        if cancellation is not None:
            raise cancellation

    async def cancel_foreground(self, operation_id: str) -> CancelResult:
        cancelled = await self._operations.cancel(operation_id)
        return CancelResult(operation_id=operation_id, cancelled=cancelled)

    async def close_application(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            shutdown_task = self._shutdown_task
            if shutdown_task is None:
                self._foreground.begin_closing()
                shutdown_task = asyncio.create_task(
                    self._close_application_once(),
                    name="local-application-close",
                    context=Context(),
                )
                self._shutdown_task = shutdown_task
        try:
            await asyncio.shield(shutdown_task)
        except asyncio.CancelledError:
            await finish_bounded_cancellation_cleanup(
                _await_shielded_task(shutdown_task),
                timeout_seconds=_APPLICATION_SHUTDOWN_CLEANUP_TIMEOUT_SECONDS,
            )
            raise

    async def _close_application_once(self) -> None:
        try:
            for delivery in tuple(self._recovery_event_deliveries):
                delivery.cancel()
            await self._operations.shutdown()
            self._foreground.cancel_exclusive()
            await self._foreground.wait_idle()
            retirements: tuple[asyncio.Task[None], ...]
            async with self._bootstrap_lock:
                runtime = self._runtime
                self._runtime = None
                if runtime is not None:
                    self._schedule_workspace_runtime_retirement(runtime)
                retirements = tuple(self._runtime_retirements.values())
            outcomes = await asyncio.gather(
                *(asyncio.shield(retirement) for retirement in retirements),
                return_exceptions=True,
            )
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    logger.warning(
                        "Workspace runtime retirement failed during shutdown.",
                        exc_info=(type(outcome), outcome, outcome.__traceback__),
                    )
        finally:
            try:
                await self._process_resources.aclose()
            finally:
                self._closed = True

    async def _reconcile_provider_credentials_before_state(self) -> None:
        if self._provider_credential_reconciled:
            return
        try:
            await asyncio.to_thread(
                reconcile_provider_credential_transaction,
                journal=self._provider_credential_journal,
                config_writer=UserConfigWriter(self._paths.config_file),
                secret_store=UserSecretStore(self._paths.env_file),
            )
        except ProviderConfigurationRecoveryRequired as error:
            raise _application_failure(
                ProductErrorCode.RECOVERY_REQUIRED,
                "Provider credential recovery could not be completed.",
                retryable=False,
                data={"state_directory": str(self._paths.home.resolve())},
            ) from error
        self._bootstrap_sources = self._load_sources(workspace_trusted=False)
        self._bootstrap_application_config = resolve_application_config(
            self._bootstrap_sources
        )
        self._provider_credential_reconciled = True

    async def _ensure_state_lease(self) -> None:
        if self._state_lease is not None and self._state_lease.active:
            return
        shared = StateLease.acquire(self._paths.home, StateLeaseMode.SHARED)
        try:
            preflight = await self._database.preflight()
        except BaseException:
            shared.close()
            raise
        if preflight.compatibility is StateCompatibility.CURRENT:
            try:
                cancellation = await _finish_state_mutation(self._database.initialize())
            except BaseException:
                shared.close()
                raise
            self._state_lease = shared
            if cancellation is not None:
                raise cancellation
            return
        shared.close()
        if preflight.compatibility not in {
            StateCompatibility.NEW,
            StateCompatibility.MIGRATION_REQUIRED,
        }:
            self._raise_preflight(preflight)

        exclusive = StateLease.acquire(
            self._paths.home,
            StateLeaseMode.EXCLUSIVE,
        )
        cancellation = None
        try:
            confirmed = await self._database.preflight()
            if confirmed.compatibility is StateCompatibility.NEW:
                cancellation = await _finish_state_mutation(self._database.initialize())
            elif confirmed.compatibility is StateCompatibility.MIGRATION_REQUIRED:
                cancellation = await _finish_state_mutation(
                    self._database.migrate(exclusive)
                )
            elif confirmed.compatibility is not StateCompatibility.CURRENT:
                self._raise_preflight(confirmed)
            try:
                exclusive.downgrade()
            except StateLeaseUnavailable:
                await _finish_state_mutation(self._database.suspend())
                raise
            initialize_cancellation = await _finish_state_mutation(
                self._database.initialize()
            )
            if cancellation is None:
                cancellation = initialize_cancellation
        except BaseException:
            exclusive.close()
            raise
        self._state_lease = exclusive
        if cancellation is not None:
            raise cancellation

    def _raise_preflight(self, preflight: StatePreflight) -> None:
        if preflight.compatibility in {
            StateCompatibility.MIGRATION_REQUIRED,
            StateCompatibility.MIGRATION_UNAVAILABLE,
            StateCompatibility.NEWER,
        }:
            assert preflight.found_schema is not None
            raise ApplicationSchemaMismatch(
                found=preflight.found_schema,
                expected=preflight.expected_schema,
                direction=preflight.compatibility,
            )
        if preflight.compatibility is StateCompatibility.UNKNOWN:
            raise ApplicationStateUnknown(self._paths.application_db)
        raise RuntimeError(
            f"Unexpected Application state preflight: {preflight.compatibility}"
        )

    def _close_state_lease(self) -> None:
        lease = self._state_lease
        self._state_lease = None
        if lease is not None:
            lease.close()

    def _ensure_workspace_leases(self) -> None:
        if (
            self._workspace_path_lease is not None
            and self._workspace_path_lease.active
            and self._workspace_entity_lease is not None
            and self._workspace_entity_lease.active
        ):
            return
        self._close_workspace_leases()
        path_lease = StateLease.acquire(
            self._paths.home / ".workspace-leases" / self._workspace.key,
            StateLeaseMode.EXCLUSIVE,
        )
        try:
            entity_lease = StateLease.acquire(
                self._paths.home
                / ".workspace-entity-leases"
                / workspace_runtime_key(self._workspace),
                StateLeaseMode.EXCLUSIVE,
            )
        except BaseException:
            path_lease.close()
            raise
        self._workspace_path_lease = path_lease
        self._workspace_entity_lease = entity_lease

    def _close_workspace_leases(self) -> None:
        entity_lease = self._workspace_entity_lease
        path_lease = self._workspace_path_lease
        self._workspace_entity_lease = None
        self._workspace_path_lease = None
        if entity_lease is not None:
            entity_lease.close()
        if path_lease is not None:
            path_lease.close()

    def _prepare_workspace_activation(self) -> None:
        try:
            self._ensure_workspace_leases()
        except StateLeaseUnavailable as error:
            raise _application_failure(
                ProductErrorCode.OPERATION_BUSY,
                "This workspace is active in another Awesome session.",
                retryable=True,
                data={"workspace_key": self._workspace.key},
            ) from error
        try:
            require_workspace_identity(self._workspace)
        except WorkspaceIdentityChanged as error:
            self._close_workspace_leases()
            raise _application_failure(
                ProductErrorCode.WORKSPACE_NOT_TRUSTED,
                "The workspace root changed after this session started. "
                "Restart Awesome.",
                data={"workspace_key": self._workspace.key},
            ) from error

    async def _activate_workspace(self) -> None:
        previous_runtime = self._runtime
        self._prepare_workspace_activation()
        try:
            await self._activate()
        except BaseException:
            if previous_runtime is None and self._runtime is None:
                self._close_workspace_leases()
            raise

    async def _activate(self) -> None:
        previous_runtime = self._runtime
        candidate = await self._build_workspace_runtime(
            selected_thread_id=(
                previous_runtime.commands.current_thread_id
                if previous_runtime is not None
                else None
            )
        )
        published = False
        try:
            self._validate_workspace_runtime(candidate)
            recovery_results = await candidate.turns.reconcile_startup()
            recovery_queue = [
                result
                for result in recovery_results
                if result.status
                in {RecoveryStatus.RESUMABLE, RecoveryStatus.INTERACTION_REQUIRED}
            ]
            self._require_runtime_publication_idle()
            self._publish_workspace_runtime(
                candidate,
                expected_previous=previous_runtime,
            )
            published = True
            self._recovery_queue = recovery_queue
            try:
                await self._present_next_recovery()
            finally:
                if previous_runtime is not None:
                    await self._close_workspace_runtime(previous_runtime)
        except BaseException:
            if not published:
                await self._close_workspace_runtime(candidate)
            raise

    async def _build_workspace_runtime(
        self,
        *,
        configuration: ProviderConfigurationSnapshot | None = None,
        selected_thread_id: str | None = None,
    ) -> WorkspaceRuntime:
        token = self._request_runtime.set(None)
        try:
            return await self._build_workspace_runtime_candidate(
                configuration=configuration,
                selected_thread_id=selected_thread_id,
            )
        finally:
            self._request_runtime.reset(token)

    async def _build_workspace_runtime_candidate(
        self,
        *,
        configuration: ProviderConfigurationSnapshot | None,
        selected_thread_id: str | None,
    ) -> WorkspaceRuntime:
        runtime_resources = RuntimeResources()
        candidate_mcp: McpManager | None = None
        try:
            if self._saver is None:
                self._saver = await self._process_resources.enter_async_context(
                    sqlite_checkpoint_saver(self._paths.checkpoint_db)
                )
                self._checkpoints = LangGraphCheckpointStore(self._saver)
            saver = self._saver
            checkpoints = self._checkpoints
            assert saver is not None
            assert checkpoints is not None
            workspace_branch = await asyncio.to_thread(
                _git_branch,
                self._workspace.canonical_path,
            )
            if configuration is None:
                try:
                    await reconcile_provider_model_transaction(
                        journal=self._provider_model_journal,
                        config_writer=UserConfigWriter(self._paths.config_file),
                        model_transactions=(self._repositories.run_write_transaction),
                        clock=self._clock,
                    )
                except ProviderConfigurationRecoveryRequired as error:
                    raise _application_failure(
                        ProductErrorCode.RECOVERY_REQUIRED,
                        "Provider configuration recovery could not be completed.",
                        retryable=False,
                        data={"state_directory": str(self._paths.state_dir.resolve())},
                    ) from error
                sources = self._load_sources(workspace_trusted=True)
                application_config = resolve_application_config(sources)
            else:
                sources, application_config = configuration
            base_gateway_factory = self._injected_gateway_factory
            if base_gateway_factory is None:
                base_gateway_factory = await runtime_resources.enter_async_context(
                    managed_gateway_factory(application_config, sources.secrets)
                )
            gateway_factory = runtime_resources.bind_gateway_factory(
                base_gateway_factory
            )
            gateway_router = runtime_resources

            change_store = SQLiteChangeSetStore(self._database)
            change_blobs = FileChangeBlobStore(self._paths.change_journal_dir)
            journal = ChangeJournal(change_store, change_blobs, self._workspace)
            change_analyzer = ChangeAnalyzer(
                change_store,
                change_blobs,
                self._workspace,
            )
            change_operations = ChangeOperations(
                change_store,
                change_blobs,
                self._workspace,
                analyzer=change_analyzer,
            )

            registry = ToolRegistry()
            register_read_tools(registry)
            register_modifying_tools(
                registry,
                journal,
                ProcessRunner(),
                workspace=self._workspace,
            )
            executor = ToolExecutor(registry)
            change_scope = ChangeScope(
                journal=journal,
                store=change_store,
                registry=registry,
                session_id=self._session_id,
                workspace=self._workspace,
            )
            await change_scope.reconcile()

            bundled = Path(__file__).parents[1] / "extensions" / "skills" / "bundled"
            catalog = discover_skills(
                bundled_root=bundled,
                user_root=self._paths.skills_dir,
                workspace_root=self._workspace.canonical_path / ".awesome" / "skills",
                workspace_trusted=True,
                workspace_anchor=self._workspace.canonical_path,
                disabled={
                    skill.name
                    for skill in (
                        *application_config.user_skills,
                        *application_config.workspace_skills,
                    )
                    if not skill.enabled
                },
            )
            skill_loader = SkillLoader(catalog)
            register_skill_tools(registry, skill_loader)

            local_memory = LocalMemoryService(
                paths=self._paths,
                workspace_key=self._workspace.key,
                enabled=application_config.memory.local_file_memory,
            )
            refresh_local_memory_tools(registry, local_memory)
            mem0_identity = self._mem0_identity(application_config)
            mem0_adapter, mem0_diagnostic = await self._create_mem0_adapter(
                sources,
                resources=runtime_resources,
            )
            mem0_session = _Mem0Session(
                enabled=application_config.memory.mem0_cloud,
                adapter=mem0_adapter,
                identity=mem0_identity,
                diagnostic=mem0_diagnostic,
            )

            enablements = SQLiteMcpEnablementStore(self._database)
            enablement_snapshot = await enablements.snapshot(self._workspace.key)
            if self._mcp_client_factory is None:
                candidate_mcp = McpManager(
                    configs=_mcp_configs(application_config),
                    workspace_trusted=True,
                    enablements=enablement_snapshot,
                    registry=registry,
                )
            else:
                candidate_mcp = McpManager(
                    configs=_mcp_configs(application_config),
                    workspace_trusted=True,
                    enablements=enablement_snapshot,
                    registry=registry,
                    client_factory=self._mcp_client_factory,
                )
            runtime_resources.push_async_callback(
                candidate_mcp.aclose,
            )

            model_catalog = ModelCatalog.from_application(application_config)
            context_model_limit = min(
                profile.context_limit for profile in model_catalog.models
            )
            context_budget = calculate_context_budget(
                application_config.budgets.total_context_tokens,
                context_model_limit,
            )
            workspace_instruction_snapshot = load_workspace_instructions(
                workspace_root=self._workspace.canonical_path,
                workspace_trusted=True,
                effective_input_limit=context_budget.effective_input_limit,
            )
            context_service = ApplicationContextService(
                conversation=self._conversation,
                workspace=self._workspace,
                builder=ContextBuilder(),
                compressor=ThreadCompressor(gateway_router),
                configured_total_tokens=application_config.budgets.total_context_tokens,
                model_context_limit=context_model_limit,
                product_instructions=CODING_AGENT_PRODUCT_INSTRUCTIONS,
                workspace_instructions=(workspace_instruction_snapshot.content or ""),
                workspace_instruction_source_id=workspace_instruction_snapshot.source_id,
                model_identity=lambda turn: ModelIdentitySnapshot.from_models(
                    configured_model=turn.model,
                    effective_model=turn.model,
                ),
                skill_loader=skill_loader,
                local_memory=local_memory,
                mem0_recall=mem0_session.recall,
            )

            graph = compile_agent_graph(saver)

            async def runtime_factory(
                turn: Turn,
                operation_id: str,
                projector: ApplicationEventProjector,
            ) -> AgentRuntimeContext:
                runtime = self._require_runtime()
                turn_id = turn.id
                budgets = turn.budgets

                async def resolve_tool_interaction(
                    request: ToolApprovalRequest,
                ) -> ToolApprovalDecision:
                    pending = self._interactions.create(
                        kind=InteractionKind.TOOL_APPROVAL,
                        prompt=request.prompt,
                        operation=request.operation,
                        target=request.target,
                        capability=request.capability,
                        choices=tool_approval_choices(request.capability),
                        thread_id=turn.thread_id,
                        turn_id=turn_id,
                        operation_id=operation_id,
                    )
                    try:
                        await self._emitter.emit(
                            InteractionRequiredPayload(
                                interaction_id=pending.id,
                                interaction_kind="tool_approval",
                                prompt=pending.prompt,
                                operation=pending.operation,
                                target=pending.target,
                                capability=pending.capability,
                                choices=tuple(
                                    InteractionChoicePayload(
                                        decision=choice.decision.value,
                                        label=choice.label,
                                        description=choice.description,
                                    )
                                    for choice in pending.choices
                                ),
                            ),
                            thread_id=turn.thread_id,
                            turn_id=turn_id,
                            operation_id=operation_id,
                        )
                    except BaseException:
                        self._interactions.discard(pending.id)
                        raise
                    decision = await self._interactions.wait(pending.id)
                    return ToolApprovalDecision(decision.value)

                async def tool_context(
                    state: object,
                    request: ToolRequest,
                ) -> ToolExecutionContext:
                    del state
                    return ToolExecutionContext(
                        workspace=self._workspace,
                        thread_id=turn.thread_id,
                        operation_id=operation_id,
                        turn_id=turn_id,
                        origin=ToolExecutionOrigin.AGENT,
                        emitter=self._emitter,
                        activity_writer=self._repositories,
                        monotonic=monotonic,
                        change_set_id=await runtime.change_scope.change_set_for_tool(
                            tool_name=request.tool_name,
                            owner=turn_id,
                            turn_id=turn_id,
                        ),
                        permission_session=self._permission_session,
                        approval_resolver=resolve_tool_interaction,
                    )

                async def record_context_snapshot(
                    manifest: tuple[dict[str, JsonValue], ...],
                ) -> None:
                    await runtime.conversation.store_context_manifest(turn.id, manifest)

                post_answer_finalizer: PostAnswerFinalizer = (
                    DisabledPostAnswerFinalizer()
                )
                if (
                    runtime.mem0_session.enabled
                    and runtime.mem0_session.adapter is not None
                    and runtime.mem0_session.identity is not None
                ):
                    post_answer_finalizer = Mem0PostAnswerFinalizer(
                        distiller=MemoryDistiller(gateway_router),
                        adapter=runtime.mem0_session.adapter,
                        identity=runtime.mem0_session.identity,
                        project_status=projector.project_memory_status,
                    )
                return AgentRuntimeContext(
                    gateway=gateway_factory(
                        cast(ProviderId, turn.provider),
                        turn.model,
                    ),
                    executor=executor,
                    tool_catalog=registry.specifications,
                    tool_context_factory=tool_context,
                    event_projector=projector,
                    context_builder=context_service.build,
                    budget=TurnBudget(
                        model_calls=budgets.model_calls,
                        tool_calls=budgets.tool_calls,
                        provider_retries=budgets.provider_retries,
                        compressions=budgets.compressions,
                        active_execution_seconds=budgets.active_execution_seconds,
                    ),
                    monotonic=monotonic,
                    context_token_estimator=estimate_messages,
                    compressor=context_service,
                    current_user_text=await context_service.runtime_current_input(turn),
                    context_snapshot_recorder=record_context_snapshot,
                    post_answer_finalizer=post_answer_finalizer,
                )

            turns = TurnCoordinator(
                workspace_key=self._workspace.key,
                conversation=self._conversation,
                config_resolver=self._turn_config,
                graph=cast(Any, graph),
                runtime_context_factory=runtime_factory,
                operations=self._operations,
                emitter=self._emitter,
                checkpoints=checkpoints,
                seal_changes=self._seal_turn,
                reconcile_changes=change_scope.reconcile,
                turn_input_preparer=context_service.prepare_turn,
                turn_extension_preparer=self._prepare_turn_extensions,
                context_snapshot_validator=context_service.validate_frozen_snapshot,
                tool_replay_safety=registry.replay_safety,
            )

            async def direct_context(
                thread_id: str,
                operation_id: str,
                request: ToolRequest,
            ) -> ToolExecutionContext:
                runtime = self._require_runtime()
                return ToolExecutionContext(
                    workspace=self._workspace,
                    thread_id=thread_id,
                    operation_id=operation_id,
                    turn_id=None,
                    origin=ToolExecutionOrigin.DIRECT,
                    emitter=self._emitter,
                    activity_writer=self._repositories,
                    monotonic=monotonic,
                    change_set_id=await runtime.change_scope.change_set_for_tool(
                        tool_name=request.tool_name,
                        owner=operation_id,
                        turn_id=None,
                    ),
                    permission_session=PermissionSession(
                        mode=PermissionMode.FULL_ACCESS
                    ),
                )

            direct = DirectCommandService(
                conversation=self._conversation,
                executor=executor,
                operations=self._operations,
                context_factory=direct_context,
                finalize_operation=self._seal_direct,
            )
            commands = ConversationCommandService(
                conversation=self._conversation,
                workspace_key=self._workspace.key,
                application_snapshot=self.application_state,
                thread_snapshot=self.thread_state,
                has_active_operation=lambda: (
                    self._operations.active_operation_id is not None
                ),
                default_model=self._initial_thread_model,
                on_thread_selected=self._on_thread_selected,
                selected_thread_id=selected_thread_id,
            )
            extensions = ApplicationExtensionService(
                conversation=self._conversation,
                catalog=catalog,
                manager=candidate_mcp,
                enablements=enablements,
                workspace_key=self._workspace.key,
                registry=registry,
                current_thread_id=lambda: (
                    self._require_runtime().commands.current_thread_id
                ),
                credential_statuses=lambda: (
                    self._require_runtime().sources.provider_credentials
                ),
                local_memory=local_memory,
                config_writer=UserConfigWriter(self._paths.config_file),
                mem0_cloud=mem0_adapter,
                mem0_enabled=application_config.memory.mem0_cloud,
                mem0_user_id=application_config.memory.mem0_user_id,
                mem0_initialization_diagnostic=mem0_diagnostic,
                mem0_state_changed=mem0_session.update,
                has_active_turn=lambda: (
                    self._operations.active_operation_id is not None
                ),
            )
            await extensions.prepare_turn_extensions()
            provider_configuration = ProviderConfigurationService(
                conversation=self._conversation,
                model_transactions=self._repositories.run_write_transaction,
                config_writer=UserConfigWriter(self._paths.config_file),
                secret_store=UserSecretStore(self._paths.env_file),
                validator=self._credential_validator,
                sources=lambda: self._require_runtime().sources,
                load_configuration=self._load_provider_configuration,
                apply_configuration=self._apply_provider_configuration,
                model_transaction_journal=self._provider_model_journal,
                credential_transaction_journal=self._provider_credential_journal,
                clock=self._clock,
            )

            async def configuration_ready() -> bool:
                return self._runtime is not None

            diagnostic_commands = DiagnosticCommandService(
                workspace_path=self._workspace.display_path,
                registry=registry,
                permission_session=self._permission_session,
                status_reader=self._command_status_snapshot,
                usage_reader=self._command_usage,
                credential_statuses=lambda: (
                    self._require_runtime().sources.provider_credentials
                ),
                provider_doctor=provider_configuration.doctor,
                configuration_ready=configuration_ready,
                sqlite_ready=self._database.quick_check,
                checkpoints_ready=checkpoints.health,
                workspace_instruction_diagnostic=lambda: (
                    self._require_runtime().workspace_instruction_snapshot.diagnostic
                ),
            )
            change_commands = ChangeCommandService(
                operations=change_operations,
                store=change_store,
                workspace_key=self._workspace.key,
            )
            permission_commands = PermissionCommandService(
                session=self._permission_session,
                operations=self._operations,
                interactions=self._interactions,
                emitter=self._emitter,
                current_thread_id=lambda: (
                    self._require_runtime().commands.current_thread_id
                ),
            )
            command_dispatcher = CommandDispatcher(
                {
                    CommandName.NEW: commands.new,
                    CommandName.RENAME: commands.rename,
                    CommandName.RESUME: commands.resume,
                    CommandName.CONTEXT: self._context_command,
                    CommandName.COMPACT: self._compact_command,
                    CommandName.AUTH: provider_configuration.auth_command,
                    CommandName.MODEL: self._model_command,
                    CommandName.THINKING: commands.thinking,
                    CommandName.WORKSPACE: diagnostic_commands.workspace,
                    CommandName.DIFF: change_commands.diff,
                    CommandName.UNDO: change_commands.undo,
                    CommandName.REDO: change_commands.redo,
                    CommandName.TOOLS: diagnostic_commands.tools,
                    CommandName.SKILLS: extensions.skills,
                    CommandName.MCP: extensions.mcp,
                    CommandName.MEMORY: extensions.memory,
                    CommandName.STATUS: diagnostic_commands.status,
                    CommandName.USAGE: diagnostic_commands.usage,
                    CommandName.DOCTOR: diagnostic_commands.doctor,
                    CommandName.CONFIG: diagnostic_commands.config,
                    CommandName.PERMISSIONS: permission_commands.permissions,
                },
                foreground=self._foreground,
                has_pending_interaction=lambda: self._interactions.pending is not None,
                mutation_guard=self._require_runtime_consistent,
            )
            return WorkspaceRuntime(
                sources=sources,
                application_config=application_config,
                conversation=self._conversation,
                turns=turns,
                commands=commands,
                command_dispatcher=command_dispatcher,
                diagnostic_commands=diagnostic_commands,
                change_commands=change_commands,
                permission_commands=permission_commands,
                provider_configuration=provider_configuration,
                direct=direct,
                extensions=extensions,
                context=context_service,
                tool_registry=registry,
                model_catalog=model_catalog,
                local_memory=local_memory,
                mem0_session=mem0_session,
                mcp=candidate_mcp,
                change_scope=change_scope,
                change_store=change_store,
                change_analyzer=change_analyzer,
                change_operations=change_operations,
                workspace_branch=workspace_branch,
                workspace_instruction_snapshot=workspace_instruction_snapshot,
                resources=runtime_resources,
            )
        except BaseException:
            await self._close_runtime_resources_best_effort(
                runtime_resources,
                reason="candidate construction",
            )
            raise

    def _validate_workspace_runtime(self, candidate: WorkspaceRuntime) -> None:
        if candidate.conversation is not self._conversation:
            raise RuntimeError("Workspace runtime owns the wrong Conversation service.")
        expected_catalog = ModelCatalog.from_application(candidate.application_config)
        if candidate.model_catalog != expected_catalog:
            raise RuntimeError("Workspace runtime Model Catalog is inconsistent.")

    def _require_runtime_publication_idle(self) -> None:
        if self._closed or self._foreground.closing:
            raise _application_failure(
                ProductErrorCode.OPERATION_BUSY,
                "Application shutdown prevents workspace runtime replacement.",
                retryable=True,
            )
        if (
            self._foreground.operation_active
            or self._operations.active_operation_id is not None
        ):
            raise _application_failure(
                ProductErrorCode.OPERATION_BUSY,
                "An active Operation prevents workspace runtime replacement.",
                retryable=True,
            )

    def _publish_workspace_runtime(
        self,
        candidate: WorkspaceRuntime,
        *,
        expected_previous: WorkspaceRuntime | None,
    ) -> None:
        if self._runtime is not expected_previous:
            raise RuntimeError("Workspace runtime changed before publication.")
        self._runtime = candidate

    async def _close_workspace_runtime(self, runtime: WorkspaceRuntime) -> None:
        retirement = self._schedule_workspace_runtime_retirement(runtime)
        if retirement is not None:
            await asyncio.shield(retirement)

    def _schedule_workspace_runtime_retirement(
        self,
        runtime: WorkspaceRuntime,
    ) -> asyncio.Task[None] | None:
        resources = runtime.resources
        if resources.closed:
            return None
        retirement = self._runtime_retirements.get(resources)
        if retirement is None:
            retirement = asyncio.create_task(
                self._retire_workspace_runtime(resources),
                name="workspace-runtime-retirement",
                context=Context(),
            )
            self._runtime_retirements[resources] = retirement
        return retirement

    async def _retire_workspace_runtime(
        self,
        resources: RuntimeResources,
    ) -> None:
        try:
            await resources.aclose()
        except BaseException as error:
            logger.warning(
                "Workspace runtime resource cleanup failed during retirement.",
                exc_info=(type(error), error, error.__traceback__),
            )
        finally:
            retirement = self._runtime_retirements.get(resources)
            if retirement is asyncio.current_task():
                self._runtime_retirements.pop(resources, None)

    async def _close_runtime_resources_best_effort(
        self,
        resources: RuntimeResources,
        *,
        reason: str,
    ) -> None:
        try:
            await resources.aclose()
        except BaseException as error:
            logger.warning(
                "Workspace runtime resource cleanup failed after %s.",
                reason,
                exc_info=(type(error), error, error.__traceback__),
            )

    def _load_sources(self, *, workspace_trusted: bool) -> LoadedConfigSources:
        return load_config_sources(
            paths=self._paths,
            workspace=self._workspace.canonical_path,
            workspace_trusted=workspace_trusted,
            environ=self._environ,
        )

    def _load_provider_configuration(self) -> ProviderConfigurationSnapshot:
        sources = self._load_sources(workspace_trusted=True)
        return sources, resolve_application_config(sources)

    async def _apply_provider_configuration(
        self,
        snapshot: ProviderConfigurationSnapshot,
        publication: ProviderConfigurationPublication,
    ) -> None:
        runtime = self._require_runtime()
        candidate = await self._build_workspace_runtime(
            configuration=snapshot,
            selected_thread_id=runtime.commands.current_thread_id,
        )
        published = False
        try:
            self._validate_workspace_runtime(candidate)
            self._require_runtime_publication_idle()
            publication.require_active()
            self._publish_workspace_runtime(
                candidate,
                expected_previous=runtime,
            )
            published = True
            # This callback runs inside the request scope bound to ``runtime``.
            # Retirement must begin now but cannot wait for that same reader.
            self._schedule_workspace_runtime_retirement(runtime)
        except BaseException:
            if not published:
                await self._close_workspace_runtime(candidate)
            raise

    def _on_thread_selected(self) -> None:
        pending = self._interactions.pending
        if (
            pending is not None
            and pending.kind is InteractionKind.FULL_ACCESS_CONFIRMATION
        ):
            self._interactions.discard(pending.id)
        self._permission_session.reset()

    async def _prepare_turn_extensions(self) -> None:
        runtime = self._require_runtime()
        await runtime.extensions.prepare_turn_extensions()

    def _turn_config(
        self,
        thread: Thread,
        *,
        runtime: WorkspaceRuntime | None = None,
    ) -> TurnConfig:
        runtime = runtime or self._require_runtime()
        selected = resolve_turn_config(
            runtime.application_config,
            thread=ThreadConfigState(
                model=thread.current_model,
                thinking_enabled=thread.thinking_enabled,
                skill_mode=thread.skill_mode,
            ),
            environ={},
        )
        model_context_limit = runtime.model_catalog.profile(
            selected.model
        ).context_limit
        return resolve_turn_config(
            runtime.application_config,
            thread=ThreadConfigState(
                model=thread.current_model,
                thinking_enabled=thread.thinking_enabled,
                skill_mode=thread.skill_mode,
            ),
            environ={},
            model_context_limit=model_context_limit,
        )

    def _model_identity(
        self,
        thread: Thread,
        *,
        runtime: WorkspaceRuntime | None = None,
    ) -> ModelIdentitySnapshot | None:
        try:
            model = self._turn_config(thread, runtime=runtime).model
        except ValueError:
            return None
        return ModelIdentitySnapshot.from_models(
            configured_model=model,
            effective_model=model,
        )

    def _initial_thread_model(self) -> str | None:
        runtime = self._require_runtime()
        selected = self._environ.get("AWESOME_MODEL")
        if selected is not None:
            return selected
        selected = runtime.application_config.providers.default_model
        if selected is not None:
            return selected
        try:
            return resolve_turn_config(
                runtime.application_config,
                thread=ThreadConfigState(),
                environ={},
            ).model
        except ValueError:
            return None

    async def _context_command(self, intent: CommandIntent) -> CommandOutcome:
        runtime = self._require_runtime()
        thread_id = self._selected_thread_id(runtime=runtime)
        if thread_id is None:
            return error("thread_not_found", "Select a Thread first.")
        return await runtime.context.context_command(intent, thread_id=thread_id)

    async def _compact_command(self, intent: CommandIntent) -> CommandOutcome:
        runtime = self._require_runtime()
        thread_id = self._selected_thread_id(runtime=runtime)
        if thread_id is None:
            return error("thread_not_found", "Select a Thread first.")
        thread = (await runtime.conversation.read_thread(thread_id)).thread
        config = self._turn_config(thread, runtime=runtime)
        return await runtime.context.compact_command(
            intent,
            thread_id=thread_id,
            provider=config.provider,
            model=config.model,
        )

    async def _model_command(self, intent: CommandIntent) -> CommandOutcome:
        runtime = self._require_runtime()
        thread_id = self._selected_thread_id(runtime=runtime)
        if thread_id is None:
            return error("thread_not_found", "Select a Thread first.")
        return await runtime.provider_configuration.model_command(
            intent,
            thread_id=thread_id,
        )

    def _selected_thread_id(
        self,
        *,
        runtime: WorkspaceRuntime | None = None,
    ) -> str | None:
        runtime = runtime or self._runtime
        return runtime.commands.current_thread_id if runtime is not None else None

    async def _command_usage(self) -> UsageSummary | None:
        runtime = self._require_runtime()
        thread_id = self._selected_thread_id(runtime=runtime)
        return (
            None
            if thread_id is None
            else await runtime.conversation.thread_usage(thread_id)
        )

    async def _command_status_snapshot(self) -> StatusSnapshot | None:
        runtime = self._require_runtime()
        thread_id = self._selected_thread_id(runtime=runtime)
        if thread_id is None:
            return None
        view = await runtime.conversation.read_thread(thread_id)
        thread = view.thread
        config = self._turn_config(thread, runtime=runtime)
        candidates = await runtime.conversation.match_thread_prefix(
            self._workspace.key,
            prefix=thread_display_id(thread.id),
            limit=200,
        )
        statuses = runtime.mcp.statuses()
        model_identity = self._model_identity(thread, runtime=runtime)
        if model_identity is None:
            return None
        credential = (
            runtime.sources.provider_credentials.deepseek
            if config.provider == "deepseek"
            else runtime.sources.provider_credentials.kimi
        )
        return StatusSnapshot(
            version=PRODUCT_VERSION,
            workspace_path=str(self._workspace.display_path),
            thread_title=thread.title,
            thread_id=thread.id,
            thread_display_id=thread_display_id(
                thread.id,
                candidate_ids=(item.id for item in candidates),
            ),
            model_identity=model_identity,
            model_status=(
                "configured"
                if self._provider_is_configured(config.provider, runtime=runtime)
                else "not_configured"
            ),
            thinking_enabled=thread.thinking_enabled,
            skill_mode=thread.skill_mode,
            local_memory_enabled=(runtime.local_memory.enabled),
            mem0_enabled=(runtime.mem0_session.enabled),
            mcp_ready=sum(
                item.state is McpConnectionState.CONNECTED for item in statuses
            ),
            mcp_degraded=sum(
                item.state is McpConnectionState.ERROR for item in statuses
            ),
            operation_status=(
                "active" if self._operations.active_operation_id else "idle"
            ),
            operation_id=self._operations.active_operation_id,
            configuration_valid=True,
            configuration_diagnostic_count=(
                1
                if runtime.mem0_session.diagnostic is not None
                and runtime.mem0_session.enabled
                else 0
            ),
            permission_mode=self._permission_session.mode,
            credential_source=credential.selected_source,
            credential_source_available=credential.source_available,
            context_used_tokens=sum(
                ContextManifestItem.model_validate(item).estimated_tokens
                for item in await runtime.conversation.latest_context_manifest(
                    thread_id
                )
            ),
            context_budget_tokens=config.budgets.total_context_tokens,
            changed_file_count=await self._latest_agent_change_file_count(
                view.tool_activities,
                runtime=runtime,
            ),
        )

    async def _latest_agent_change_file_count(
        self,
        activities: tuple[ToolActivity, ...],
        *,
        runtime: WorkspaceRuntime,
    ) -> int:
        seen: set[str] = set()
        for activity in reversed(activities):
            identifier = activity.change_set_id
            if (
                activity.origin is not ToolActivityOrigin.AGENT
                or identifier is None
                or identifier in seen
            ):
                continue
            seen.add(identifier)
            change_set = await runtime.change_store.get(identifier)
            if (
                change_set is None
                or change_set.workspace_key != self._workspace.key
                or change_set.turn_id != activity.turn_id
                or change_set.lifecycle is ChangeLifecycle.OPEN
            ):
                continue
            if change_set.lifecycle is ChangeLifecycle.UNDONE:
                return 0
            try:
                return len(merge_file_changes(change_set.files))
            except ChangeLifecycleError:
                continue
        return 0

    def _mem0_identity(
        self,
        application_config: ApplicationConfig,
    ) -> Mem0Identity | None:
        user_id = application_config.memory.mem0_user_id
        if user_id is None:
            return None
        return Mem0Identity(user_id=user_id, workspace_key=self._workspace.key)

    async def _create_mem0_adapter(
        self,
        sources: LoadedConfigSources,
        *,
        resources: RuntimeResources,
    ) -> tuple[Mem0CloudAdapter | None, Mem0Diagnostic | None]:
        client = self._injected_mem0_client
        if client is None:
            secret = sources.secrets.mem0_api_key
            try:
                client = await resources.enter_async_context(
                    managed_mem0_client(
                        secret.get_secret_value() if secret is not None else None
                    )
                )
            except Mem0CloudError as error:
                return None, error.diagnostic
        return Mem0CloudAdapter(cast(Mem0Client, client)), None

    async def _seal_turn(self, turn_id: str) -> None:
        runtime = self._request_runtime.get() or self._runtime
        if runtime is not None:
            await runtime.change_scope.seal(turn_id)

    async def _seal_direct(self, operation_id: str) -> None:
        runtime = self._request_runtime.get() or self._runtime
        if runtime is not None:
            await runtime.change_scope.seal(operation_id)

    async def _require_runtime_consistent(
        self,
        runtime: WorkspaceRuntime | None = None,
    ) -> None:
        runtime = runtime or self._require_runtime()
        try:
            await runtime.provider_configuration.ensure_consistent()
        except ProviderConfigurationRecoveryRequired as error:
            raise _application_failure(
                ProductErrorCode.RECOVERY_REQUIRED,
                "Provider configuration recovery is required. Restart Awesome.",
                retryable=False,
            ) from error
        except ResourceLockTimeout as error:
            raise _application_failure(
                ProductErrorCode.OPERATION_BUSY,
                "User state is being changed by another Awesome process.",
                retryable=True,
            ) from error
        except ResourceLockUnavailable as error:
            raise _application_failure(
                ProductErrorCode.STATE_UNAVAILABLE,
                "User state cannot be accessed safely.",
                retryable=True,
                data={"state_directory": str(self._paths.state_dir.resolve())},
            ) from error

    def _require_runtime(self) -> WorkspaceRuntime:
        bound = self._request_runtime.get()
        if bound is not None:
            return bound
        self._require_open()
        runtime = self._runtime
        if runtime is None:
            raise _application_failure(
                ProductErrorCode.WORKSPACE_NOT_TRUSTED,
                "Trust the workspace before using project capabilities.",
            )
        return runtime

    @contextmanager
    def _runtime_request_scope(
        self,
        runtime: WorkspaceRuntime,
    ) -> Iterator[WorkspaceRuntime]:
        bound = self._request_runtime.get()
        if bound is not None:
            yield bound
            return
        with runtime.resources.reader():
            token = self._request_runtime.set(runtime)
            try:
                yield runtime
            finally:
                self._request_runtime.reset(token)

    def _require_open(self) -> None:
        if self._closed or self._foreground.closing:
            raise _application_failure(
                ProductErrorCode.OPERATION_BUSY,
                "Application is shutting down.",
            )

    def _require_provider_configured(
        self,
        provider: ProviderId,
        *,
        runtime: WorkspaceRuntime,
    ) -> None:
        if not self._provider_is_configured(provider, runtime=runtime):
            raise _application_failure(
                ProductErrorCode.PROVIDER_NOT_CONFIGURED,
                f"{provider} credentials are not configured.",
                data={"provider": provider},
            )

    def _require_selected_thread(
        self,
        thread_id: str,
        *,
        runtime: WorkspaceRuntime,
    ) -> None:
        if self._selected_thread_id(runtime=runtime) != thread_id:
            raise _application_failure(
                ProductErrorCode.INVALID_ARGUMENTS,
                "Select the target Thread before starting an operation.",
            )

    def _provider_is_configured(
        self,
        provider: ProviderId,
        *,
        runtime: WorkspaceRuntime,
    ) -> bool:
        status = runtime.sources.secret_status
        return (
            status.deepseek_api_key
            if provider == "deepseek"
            else status.moonshot_api_key
        )

    def _workspace_presentation(
        self,
        *,
        include_branch: bool,
        runtime: WorkspaceRuntime | None = None,
    ) -> WorkspacePresentation:
        runtime = runtime or self._request_runtime.get() or self._runtime
        return WorkspacePresentation(
            display_path=str(self._workspace.display_path),
            branch=runtime.workspace_branch if include_branch and runtime else None,
        )

    async def _page_change_summaries(
        self,
        activities: tuple[ToolActivity, ...],
        *,
        runtime: WorkspaceRuntime,
    ) -> tuple[ChangeSetSummary, ...]:
        summaries: list[ChangeSetSummary] = []
        seen: set[str] = set()
        for activity in activities:
            change_set_id = activity.change_set_id
            if change_set_id is None or change_set_id in seen:
                continue
            seen.add(change_set_id)
            change_set = await runtime.change_store.get(change_set_id)
            if change_set is None:
                continue
            analysis = await runtime.change_analyzer.analyze(change_set_id)
            if not analysis.changes:
                continue
            summaries.append(
                ChangeSetSummary(
                    change_set_id=change_set.id,
                    turn_id=change_set.turn_id,
                    operation_id=activity.operation_id,
                    lifecycle=change_set.lifecycle.value,
                    changes=analysis.changes,
                    created_at=change_set.created_at,
                    sealed_at=change_set.sealed_at,
                )
            )
        return tuple(summaries)


async def _await_shielded_task(task: asyncio.Task[None]) -> None:
    await asyncio.shield(task)


async def _finish_state_mutation[ResultT](
    operation: Awaitable[ResultT],
) -> asyncio.CancelledError | None:
    """Finish a lease-bound state mutation before exposing caller cancellation."""

    _, cancellation = await finish_cancellation_safe(operation)
    return cancellation


async def _finish_cancelled_worker(worker: asyncio.Task[None]) -> None:
    """Keep uncancellable thread work owned until it has actually stopped."""

    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    if not worker.cancelled():
        with suppress(Exception):
            worker.result()


def _consume_background_task_result(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    with suppress(Exception):
        task.result()


def _mcp_configs(config: ApplicationConfig) -> tuple[McpServerConfig, ...]:
    user = tuple(
        McpServerConfig(
            id=item.id,
            command=item.command,
            args=item.args,
            env_names=item.env,
            source=McpSource.USER,
            enabled=item.enabled,
        )
        for item in config.user_mcp_servers
    )
    workspace = tuple(
        McpServerConfig(
            id=item.id,
            command=item.command,
            args=item.args,
            env_names=item.env,
            source=McpSource.WORKSPACE,
        )
        for item in config.workspace_mcp_servers
    )
    return (*user, *workspace)


def _application_failure(
    code: ProductErrorCode,
    message: str,
    *,
    retryable: bool = False,
    data: dict[str, JsonValue] | None = None,
) -> ApplicationFailure:
    return ApplicationFailure(
        ProductError(
            code=code,
            message=message,
            retryable=retryable,
            data=data or {},
        )
    )


def _git_branch(workspace: Path) -> str | None:
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(workspace),
                "symbolic-ref",
                "--quiet",
                "--short",
                "HEAD",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    branch = completed.stdout.strip()
    return branch or None
