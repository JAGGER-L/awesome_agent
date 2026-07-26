from __future__ import annotations

import asyncio
import logging
import subprocess
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import JsonValue

from awesome_agent.agent import (
    AgentRuntimeContext,
    CloudPostAnswerMemory,
    DisabledPostAnswerMemory,
    PostAnswerMemory,
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
from awesome_agent.application.operations import (
    OperationBusy,
    OperationContinuation,
    OperationController,
)
from awesome_agent.application.permission_commands import PermissionCommandService
from awesome_agent.application.provider_configuration import (
    CredentialValidator,
    ProviderConfigurationRecoveryRequired,
    ProviderConfigurationService,
    ProviderConfigurationSnapshot,
    reconcile_provider_credential_transaction,
    reconcile_provider_model_transaction,
)
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
    MemoryDistiller,
    create_mem0_client,
    refresh_local_memory_tools,
)
from awesome_agent.memory.mem0_cloud import Mem0Client
from awesome_agent.modeling import (
    GatewayEvent,
    ModelCatalog,
    ModelGateway,
    ModelIdentitySnapshot,
    ModelProvider,
    ModelRequest,
    ModelTurn,
    ProviderId,
    RetryPolicy,
    SelectedModel,
)
from awesome_agent.paths import AwesomePaths
from awesome_agent.providers import (
    DeepSeekProvider,
    KimiProvider,
    ProviderCredentialValidator,
)
from awesome_agent.storage import (
    ApplicationSchemaMismatch,
    ApplicationStateUnavailable,
    ApplicationStateUnknown,
    SQLiteMcpEnablementStore,
    StateCompatibility,
    StateLease,
    StateLeaseMode,
    StateLeaseUnavailable,
    StatePreflight,
    StateResetError,
    initialize_application_database,
    inspect_application_state,
    reset_local_state,
)
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore
from awesome_agent.storage.checkpoints import (
    LangGraphCheckpointStore,
    sqlite_checkpoint_saver,
)
from awesome_agent.storage.conversations import SQLiteConversationRepositories
from awesome_agent.storage.health import sqlite_database_health
from awesome_agent.storage.pagination import (
    InvalidThreadCursor,
    decode_thread_cursor,
    encode_thread_cursor,
)
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore
from awesome_agent.version import PRODUCT_VERSION

type GatewayFactory = Callable[[ProviderId, str], ModelGateway]
type McpClientFactory = Callable[[McpServerConfig], McpClient]

logger = logging.getLogger(__name__)

_MAX_THREAD_RESULT_BYTES = 900_000
_ACTIVATION_ROLLBACK_TIMEOUT_SECONDS = 5.0
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


_ACTIVATION_STATE_FIELDS = (
    "_sources",
    "_application_config",
    "_initialized",
    "_commands",
    "_command_dispatcher",
    "_diagnostic_commands",
    "_change_commands",
    "_permission_commands",
    "_provider_configuration",
    "_turns",
    "_direct",
    "_extensions",
    "_context",
    "_registry",
    "_local_memory",
    "_mem0_adapter",
    "_mem0_diagnostic",
    "_mem0_session",
    "_mcp",
    "_change_scope",
    "_change_store",
    "_change_analyzer",
    "_change_operations",
    "_workspace_branch",
    "_workspace_instruction_snapshot",
    "_recovery_queue",
    "_recovery_resolution_delivery",
    "_recovery_required_delivery_id",
)

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
    stack = AsyncExitStack()
    backend = _LocalApplicationBackend(
        paths=paths,
        workspace=identity,
        event_sink=event_sink,
        resources=stack,
        environ=environ,
        gateway_factory=gateway_factory,
        mcp_client_factory=mcp_client_factory,
        mem0_client=mem0_client,
        credential_validator=credential_validator,
    )
    return LocalApplication(backend)


class _GatewayRouter:
    def __init__(self, factory: GatewayFactory) -> None:
        self._factory = factory

    async def complete(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> ModelTurn:
        return await self._factory(selected.provider, selected.model).complete(
            selected,
            request,
        )

    async def stream(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> AsyncIterator[GatewayEvent]:
        async for event in self._factory(selected.provider, selected.model).stream(
            selected,
            request,
        ):
            yield event


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


class _LocalApplicationBackend:
    def __init__(
        self,
        *,
        paths: AwesomePaths,
        workspace: WorkspaceIdentity,
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
        self._resources = resources
        self._environ = dict(environ or {})
        self._injected_gateway_factory = gateway_factory
        self._mcp_client_factory = mcp_client_factory
        self._injected_mem0_client = mem0_client
        self._credential_validator = (
            credential_validator or ProviderCredentialValidator()
        )
        self._session_id = new_identifier("session")
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
        self._trust = WorkspaceTrustService(
            SQLiteWorkspaceTrustStore(paths.application_db)
        )
        self._repositories = SQLiteConversationRepositories(paths.application_db)
        self._conversation = ConversationService(store=self._repositories)
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
        self._sources = LoadedConfigSources(
            user=UserConfigDocument(),
            workspace=None,
            secrets=SecretValues(),
            secret_status=SecretStatus(),
            provider_credentials=missing_provider_credential_statuses(),
        )
        self._application_config = resolve_application_config(self._sources)
        self._initialized = False
        self._closed = False
        self._commands: ConversationCommandService | None = None
        self._command_dispatcher: CommandDispatcher | None = None
        self._diagnostic_commands: DiagnosticCommandService | None = None
        self._change_commands: ChangeCommandService | None = None
        self._permission_commands: PermissionCommandService | None = None
        self._provider_configuration: ProviderConfigurationService | None = None
        self._turns: TurnCoordinator | None = None
        self._direct: DirectCommandService | None = None
        self._extensions: ApplicationExtensionService | None = None
        self._context: ApplicationContextService | None = None
        self._registry: ToolRegistry | None = None
        self._local_memory: LocalMemoryService | None = None
        self._mem0_adapter: Mem0CloudAdapter | None = None
        self._mem0_diagnostic: Mem0Diagnostic | None = None
        self._mem0_session: _Mem0Session | None = None
        self._mcp: McpManager | None = None
        self._change_scope: ChangeScope | None = None
        self._change_store: SQLiteChangeSetStore | None = None
        self._change_analyzer: ChangeAnalyzer | None = None
        self._change_operations: ChangeOperations | None = None
        self._workspace_branch: str | None = None
        self._workspace_instruction_snapshot: WorkspaceInstructionSnapshot | None = None
        self._recovery_queue: list[RecoveryResult] = []
        self._recovery_resolution_delivery: _RecoveryResolutionDelivery | None = None
        self._recovery_required_delivery_id: str | None = None
        self._recovery_required_delivery_lock = asyncio.Lock()
        self._permission_session = PermissionSession()
        self._state_lease: StateLease | None = None
        self._workspace_path_lease: StateLease | None = None
        self._workspace_entity_lease: StateLease | None = None
        self._bootstrap_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._resources.callback(self._close_state_lease)
        self._resources.callback(self._close_workspace_leases)

    async def initialize_application(self) -> InitializeResult:
        async with self._bootstrap_lock:
            if self._closed or self._foreground.closing:
                raise _application_failure(
                    ProductErrorCode.INTERNAL_ERROR,
                    "Application is shutting down.",
                )
            return await self._initialize_application_locked()

    async def _initialize_application_locked(self) -> InitializeResult:
        if self._initialized:
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
            self._ensure_state_lease()
            trust_status = self._trust.status(self._workspace)
        except ApplicationSchemaMismatch as error:
            if error.direction is StateCompatibility.OLDER:
                return await self._state_reset_required()
            raise self._newer_state_failure(error.found, error.expected) from error
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
        current_id = (
            self._commands.current_thread_id
            if self._initialized and self._commands
            else None
        )
        current = (
            self._conversation.read_thread(current_id).thread
            if current_id is not None
            else None
        )
        usage = (
            self._conversation.thread_usage(current_id)
            if current_id is not None
            else UsageSummary()
        )
        local_enabled = self._local_memory.enabled if self._local_memory else False
        return ApplicationState(
            initialized=self._initialized,
            session_id=self._session_id,
            workspace_key=self._workspace.key,
            workspace=self._workspace_presentation(include_branch=True),
            workspace_trusted=self._initialized,
            current_thread_id=current_id,
            model_identity=self._model_identity(current) if current else None,
            thinking_enabled=current.thinking_enabled if current else True,
            skill_mode=current.skill_mode if current else "auto",
            active_operation_id=self._operations.active_operation_id,
            pending_interaction_id=(
                self._interactions.pending.id if self._interactions.pending else None
            ),
            permission_mode=self._permission_session.mode,
            configuration_valid=True,
            secret_status=self._sources.secret_status,
            provider_credentials=self._sources.provider_credentials,
            memory_status={
                "local": {"enabled": local_enabled},
                "mem0": {
                    "enabled": (
                        self._mem0_session.enabled
                        if self._mem0_session is not None
                        else self._application_config.memory.mem0_cloud
                    ),
                    "available": self._mem0_adapter is not None,
                },
            },
            mcp_status=tuple(
                {
                    "server_id": status.server_id,
                    "state": status.state.value,
                }
                for status in (self._mcp.statuses() if self._mcp else ())
            ),
            usage=usage.model_dump(mode="json"),
            configuration_diagnostics=(
                (self._mem0_diagnostic.code,)
                if (
                    self._mem0_diagnostic is not None
                    and self._mem0_session is not None
                    and self._mem0_session.enabled
                )
                else ()
            ),
            workspace_instruction_diagnostic=(
                self._workspace_instruction_snapshot.diagnostic
                if self._workspace_instruction_snapshot is not None
                else None
            ),
        )

    async def workspace_threads(self, query: ThreadListQuery) -> ThreadListResult:
        self._require_active()
        try:
            cursor = (
                decode_thread_cursor(query.cursor) if query.cursor is not None else None
            )
        except InvalidThreadCursor as error:
            raise _application_failure(
                ProductErrorCode.INVALID_ARGUMENTS,
                "Thread cursor is invalid.",
            ) from error
        page = self._conversation.list_thread_page(
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
        self._require_active()
        limit = query.limit
        while True:
            try:
                page = self._conversation.read_thread_page(
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
                change_sets=self._page_change_summaries(page.view.tool_activities),
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
        self._require_active()
        self._require_selected_thread(thread_id)
        assert self._turns is not None
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
            await self._require_runtime_consistent()
            thread = self._conversation.read_thread(thread_id).thread
            config = self._turn_config(thread)
            self._require_provider_configured(config.provider)
            return await self._turns.submit_turn(
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
        self._require_active()
        self._require_selected_thread(thread_id)
        assert self._direct is not None
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
            await self._require_runtime_consistent()
            return await self._direct.start(thread_id, command)
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
        self._require_active()
        assert self._command_dispatcher is not None
        try:
            return await self._command_dispatcher.dispatch(intent)
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
        self._require_active()
        assert self._provider_configuration is not None
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
                await self._require_runtime_consistent()
                return await self._provider_configuration.set_credential(request)
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
        if self._initialized:
            await self._require_runtime_consistent()
        pending = self._interactions.pending
        if pending is None or pending.id != interaction_id:
            return InteractionResult(accepted=False, status="not_found")
        try:
            parsed = InteractionDecision(decision)
        except ValueError:
            return InteractionResult(accepted=False, status="invalid_decision")
        if pending.kind is InteractionKind.RECOVERY_DECISION:
            async with self._bootstrap_lock:
                return await self._resolve_recovery_interaction(
                    pending,
                    parsed,
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
                    self._trust.accept(self._workspace)
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
                self._commands.current_thread_id if self._commands is not None else None
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
                    self._commands.current_thread_id
                    if self._commands is not None
                    else None
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
    ) -> InteractionResult:
        if decision is InteractionDecision.RETRY:
            return await self._retry_recovery(pending)
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
            assert self._turns is not None
            try:
                await self._turns.abort_recovery(
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
    ) -> InteractionResult:
        recovery = self._bound_recovery(pending)
        if recovery is None:
            self._interactions.discard(pending.id)
            return InteractionResult(accepted=False, status="stale")
        assert self._turns is not None
        assert self._commands is not None
        turns = self._turns
        commands = self._commands
        claimed = False
        resolution_published = asyncio.Event()

        def claim(turn: Turn) -> None:
            nonlocal claimed
            current = self._bound_recovery(pending)
            if current != recovery or turn.id != recovery.turn_id:
                raise TurnExecutionFailed("recovery_stale")
            commands.select_recovery_thread(recovery.thread_id)
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
            if claimed and self._recovery_is_in_progress(recovery):
                self._recovery_queue.insert(0, recovery)
            elif not claimed:
                self._discard_recovery(pending, recovery)
            await self._present_next_recovery()
            if response_cancellation is not None:
                raise response_cancellation from None
            return InteractionResult(accepted=False, status="stale")
        except asyncio.CancelledError:
            resolution_published.set()
            if claimed and self._recovery_is_in_progress(recovery):
                self._recovery_queue.insert(0, recovery)
                await self._present_next_recovery()
            if response_cancellation is not None:
                raise response_cancellation from None
            raise
        except BaseException:
            resolution_published.set()
            if claimed and self._recovery_is_in_progress(recovery):
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

    def _recovery_is_in_progress(self, recovery: RecoveryResult) -> bool:
        try:
            view = self._conversation.read_thread(recovery.thread_id)
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

        worker: asyncio.Task[None] | None = None
        try:
            preflight = inspect_application_state(self._paths.application_db)
            if preflight.compatibility is StateCompatibility.OLDER:
                worker = asyncio.create_task(
                    asyncio.to_thread(reset_local_state, exclusive)
                )
                await asyncio.shield(worker)
            elif preflight.compatibility is StateCompatibility.NEW:
                worker = asyncio.create_task(
                    asyncio.to_thread(
                        initialize_application_database,
                        self._paths.application_db,
                    )
                )
                await asyncio.shield(worker)
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
            exclusive.downgrade()
        except asyncio.CancelledError:
            if worker is not None:
                await _finish_cancelled_worker(worker)
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
        self._state_lease = exclusive

    async def cancel_foreground(self, operation_id: str) -> CancelResult:
        cancelled = await self._operations.cancel(operation_id)
        return CancelResult(operation_id=operation_id, cancelled=cancelled)

    async def close_application(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._foreground.begin_closing()
            await self._operations.shutdown()
            self._foreground.cancel_exclusive()
            await self._foreground.wait_idle()
            async with self._bootstrap_lock:
                if self._mcp is not None:
                    await self._mcp.aclose()
                await self._resources.aclose()
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
        self._sources = self._load_sources(workspace_trusted=False)
        self._application_config = resolve_application_config(self._sources)
        self._provider_credential_reconciled = True

    def _ensure_state_lease(self) -> None:
        if self._state_lease is not None and self._state_lease.active:
            return
        shared = StateLease.acquire(self._paths.home, StateLeaseMode.SHARED)
        try:
            preflight = inspect_application_state(self._paths.application_db)
        except Exception:
            shared.close()
            raise
        if preflight.compatibility is StateCompatibility.CURRENT:
            self._state_lease = shared
            return
        shared.close()
        if preflight.compatibility is not StateCompatibility.NEW:
            self._raise_preflight(preflight)

        exclusive = StateLease.acquire(
            self._paths.home,
            StateLeaseMode.EXCLUSIVE,
        )
        try:
            confirmed = inspect_application_state(self._paths.application_db)
            if confirmed.compatibility is StateCompatibility.NEW:
                initialize_application_database(self._paths.application_db)
            elif confirmed.compatibility is not StateCompatibility.CURRENT:
                self._raise_preflight(confirmed)
            exclusive.downgrade()
        except Exception:
            exclusive.close()
            raise
        self._state_lease = exclusive

    def _raise_preflight(self, preflight: StatePreflight) -> None:
        if preflight.compatibility in {
            StateCompatibility.OLDER,
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
        self._prepare_workspace_activation()
        try:
            await self._activate()
        except BaseException:
            self._close_workspace_leases()
            raise

    async def _activate(self) -> None:
        if self._initialized:
            return
        snapshot = {field: getattr(self, field) for field in _ACTIVATION_STATE_FIELDS}
        try:
            await self._activate_candidate()
        except BaseException:
            candidate_mcp = self._mcp
            for field, value in snapshot.items():
                setattr(self, field, value)
            if candidate_mcp is not None and candidate_mcp is not snapshot["_mcp"]:
                await self._close_activation_candidate(candidate_mcp)
            raise

    async def _activate_candidate(self) -> None:
        if self._saver is None:
            self._saver = await self._resources.enter_async_context(
                sqlite_checkpoint_saver(self._paths.checkpoint_db)
            )
            self._checkpoints = LangGraphCheckpointStore(self._saver)
        saver = self._saver
        checkpoints = self._checkpoints
        assert saver is not None
        assert checkpoints is not None
        self._workspace_branch = await asyncio.to_thread(
            _git_branch,
            self._workspace.canonical_path,
        )
        try:
            await asyncio.to_thread(
                reconcile_provider_model_transaction,
                journal=self._provider_model_journal,
                config_writer=UserConfigWriter(self._paths.config_file),
                conversation=self._conversation,
            )
        except ProviderConfigurationRecoveryRequired as error:
            raise _application_failure(
                ProductErrorCode.RECOVERY_REQUIRED,
                "Provider configuration recovery could not be completed.",
                retryable=False,
                data={"state_directory": str(self._paths.state_dir.resolve())},
            ) from error
        self._sources = self._load_sources(workspace_trusted=True)
        self._application_config = resolve_application_config(self._sources)
        gateway_factory = self._injected_gateway_factory or self._provider_factory()
        gateway_router = _GatewayRouter(gateway_factory)

        change_store = SQLiteChangeSetStore(self._paths.application_db)
        self._change_store = change_store
        change_blobs = FileChangeBlobStore(self._paths.change_journal_dir)
        journal = ChangeJournal(change_store, change_blobs, self._workspace)
        self._change_analyzer = ChangeAnalyzer(
            change_store,
            change_blobs,
            self._workspace,
        )
        self._change_operations = ChangeOperations(
            change_store,
            change_blobs,
            self._workspace,
            analyzer=self._change_analyzer,
        )

        registry = ToolRegistry()
        register_read_tools(registry)
        register_modifying_tools(registry, journal, ProcessRunner())
        self._registry = registry
        executor = ToolExecutor(registry)
        self._change_scope = ChangeScope(
            journal=journal,
            store=change_store,
            registry=registry,
            session_id=self._session_id,
            workspace=self._workspace,
        )
        self._change_scope.reconcile()

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
                    *self._application_config.user_skills,
                    *self._application_config.workspace_skills,
                )
                if not skill.enabled
            },
        )
        skill_loader = SkillLoader(catalog)
        register_skill_tools(registry, skill_loader)

        enablements = SQLiteMcpEnablementStore(self._paths.application_db)
        if self._mcp_client_factory is None:
            self._mcp = McpManager(
                configs=_mcp_configs(self._application_config),
                workspace_key=self._workspace.key,
                workspace_trusted=True,
                enablements=enablements,
                registry=registry,
            )
        else:
            self._mcp = McpManager(
                configs=_mcp_configs(self._application_config),
                workspace_key=self._workspace.key,
                workspace_trusted=True,
                enablements=enablements,
                registry=registry,
                client_factory=self._mcp_client_factory,
            )

        self._local_memory = LocalMemoryService(
            paths=self._paths,
            workspace_key=self._workspace.key,
            enabled=self._application_config.memory.local_file_memory,
        )
        refresh_local_memory_tools(registry, self._local_memory)
        mem0_identity = self._mem0_identity()
        self._mem0_adapter = self._create_mem0_adapter()
        self._mem0_session = _Mem0Session(
            enabled=self._application_config.memory.mem0_cloud,
            adapter=self._mem0_adapter,
            identity=mem0_identity,
            diagnostic=self._mem0_diagnostic,
        )

        model_catalog = ModelCatalog.from_application(self._application_config)
        context_model_limit = min(
            profile.context_limit for profile in model_catalog.models
        )
        context_budget = calculate_context_budget(
            self._application_config.budgets.total_context_tokens,
            context_model_limit,
        )
        self._workspace_instruction_snapshot = load_workspace_instructions(
            workspace_root=self._workspace.canonical_path,
            workspace_trusted=True,
            effective_input_limit=context_budget.effective_input_limit,
        )
        context_service = ApplicationContextService(
            conversation=self._conversation,
            workspace=self._workspace,
            builder=ContextBuilder(),
            compressor=ThreadCompressor(gateway_router),
            configured_total_tokens=self._application_config.budgets.total_context_tokens,
            model_context_limit=context_model_limit,
            product_instructions=CODING_AGENT_PRODUCT_INSTRUCTIONS,
            workspace_instructions=(self._workspace_instruction_snapshot.content or ""),
            workspace_instruction_source_id=(
                self._workspace_instruction_snapshot.source_id
            ),
            model_identity=lambda turn: ModelIdentitySnapshot.from_models(
                configured_model=turn.model,
                effective_model=turn.model,
            ),
            skill_loader=skill_loader,
            local_memory=self._local_memory,
            mem0_recall=self._mem0_session.recall,
        )
        self._context = context_service

        graph = compile_agent_graph(saver)

        def runtime_factory(
            turn: Turn,
            operation_id: str,
            projector: ApplicationEventProjector,
        ) -> AgentRuntimeContext:
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

            def tool_context(
                state: object,
                request: ToolRequest,
            ) -> ToolExecutionContext:
                del state
                assert self._change_scope is not None
                return ToolExecutionContext(
                    workspace=self._workspace,
                    thread_id=turn.thread_id,
                    operation_id=operation_id,
                    turn_id=turn_id,
                    origin=ToolExecutionOrigin.AGENT,
                    emitter=self._emitter,
                    activity_writer=self._repositories.tool_activities,
                    monotonic=monotonic,
                    change_set_id=self._change_scope.change_set_for_tool(
                        tool_name=request.tool_name,
                        owner=turn_id,
                        turn_id=turn_id,
                    ),
                    permission_session=self._permission_session,
                    approval_resolver=resolve_tool_interaction,
                )

            def record_context_snapshot(
                manifest: tuple[dict[str, JsonValue], ...],
            ) -> None:
                self._conversation.store_context_manifest(turn.id, manifest)

            post_answer_memory: PostAnswerMemory = DisabledPostAnswerMemory()
            if (
                self._mem0_session is not None
                and self._mem0_session.enabled
                and self._mem0_session.adapter is not None
                and self._mem0_session.identity is not None
            ):
                post_answer_memory = CloudPostAnswerMemory(
                    distiller=MemoryDistiller(gateway_router),
                    adapter=self._mem0_session.adapter,
                    identity=self._mem0_session.identity,
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
                current_user_text=context_service.runtime_current_input(turn),
                context_snapshot_recorder=record_context_snapshot,
                post_answer_memory=post_answer_memory,
            )

        self._turns = TurnCoordinator(
            workspace_key=self._workspace.key,
            conversation=self._conversation,
            config_resolver=self._turn_config,
            graph=cast(Any, graph),
            runtime_context_factory=runtime_factory,
            operations=self._operations,
            emitter=self._emitter,
            checkpoints=checkpoints,
            seal_changes=self._seal_turn,
            reconcile_changes=self._change_scope.reconcile,
            turn_input_preparer=context_service.prepare_turn,
            turn_extension_preparer=self._prepare_turn_extensions,
            context_snapshot_validator=context_service.validate_frozen_snapshot,
        )

        def direct_context(
            thread_id: str,
            operation_id: str,
            request: ToolRequest,
        ) -> ToolExecutionContext:
            assert self._change_scope is not None
            return ToolExecutionContext(
                workspace=self._workspace,
                thread_id=thread_id,
                operation_id=operation_id,
                turn_id=None,
                origin=ToolExecutionOrigin.DIRECT,
                emitter=self._emitter,
                activity_writer=self._repositories.tool_activities,
                monotonic=monotonic,
                change_set_id=self._change_scope.change_set_for_tool(
                    tool_name=request.tool_name,
                    owner=operation_id,
                    turn_id=None,
                ),
                permission_session=PermissionSession(mode=PermissionMode.FULL_ACCESS),
            )

        self._direct = DirectCommandService(
            conversation=self._conversation,
            executor=executor,
            operations=self._operations,
            context_factory=direct_context,
            finalize_operation=self._seal_direct,
        )
        assert self._mem0_session is not None
        self._commands = ConversationCommandService(
            conversation=self._conversation,
            workspace_key=self._workspace.key,
            application_snapshot=self.application_state,
            thread_snapshot=self.thread_state,
            has_active_operation=lambda: (
                self._operations.active_operation_id is not None
            ),
            default_model=self._initial_thread_model,
            on_thread_selected=self._on_thread_selected,
        )
        self._extensions = ApplicationExtensionService(
            conversation=self._conversation,
            catalog=catalog,
            manager=self._mcp,
            enablements=enablements,
            workspace_key=self._workspace.key,
            registry=registry,
            current_thread_id=lambda: (
                self._commands.current_thread_id if self._commands is not None else None
            ),
            credential_statuses=lambda: self._sources.provider_credentials,
            local_memory=self._local_memory,
            config_writer=UserConfigWriter(self._paths.config_file),
            mem0_cloud=self._mem0_adapter,
            mem0_enabled=self._application_config.memory.mem0_cloud,
            mem0_user_id=self._application_config.memory.mem0_user_id,
            mem0_initialization_diagnostic=self._mem0_diagnostic,
            mem0_state_changed=self._mem0_session.update,
            has_active_turn=lambda: self._operations.active_operation_id is not None,
        )
        await self._extensions.prepare_turn_extensions()
        self._provider_configuration = ProviderConfigurationService(
            conversation=self._conversation,
            config_writer=UserConfigWriter(self._paths.config_file),
            secret_store=UserSecretStore(self._paths.env_file),
            validator=self._credential_validator,
            sources=lambda: self._sources,
            load_configuration=self._load_provider_configuration,
            apply_configuration=self._apply_provider_configuration,
            model_transaction_journal=self._provider_model_journal,
            credential_transaction_journal=self._provider_credential_journal,
        )
        self._diagnostic_commands = DiagnosticCommandService(
            workspace_path=self._workspace.display_path,
            registry=registry,
            permission_session=self._permission_session,
            status_reader=self._command_status_snapshot,
            usage_reader=self._command_usage,
            credential_statuses=lambda: self._sources.provider_credentials,
            provider_doctor=self._provider_configuration.doctor,
            configuration_ready=lambda: self._initialized,
            sqlite_ready=lambda: sqlite_database_health(self._paths.application_db),
            checkpoints_ready=lambda: sqlite_database_health(
                self._paths.checkpoint_db
            ),
            workspace_instruction_diagnostic=lambda: (
                self._workspace_instruction_snapshot.diagnostic
                if self._workspace_instruction_snapshot is not None
                else None
            ),
        )
        assert self._change_operations is not None
        assert self._change_store is not None
        self._change_commands = ChangeCommandService(
            operations=self._change_operations,
            store=self._change_store,
            workspace_key=self._workspace.key,
        )
        self._permission_commands = PermissionCommandService(
            session=self._permission_session,
            operations=self._operations,
            interactions=self._interactions,
            emitter=self._emitter,
            current_thread_id=lambda: (
                self._commands.current_thread_id if self._commands is not None else None
            ),
        )
        self._command_dispatcher = CommandDispatcher(
            {
                CommandName.NEW: self._commands.new,
                CommandName.RENAME: self._commands.rename,
                CommandName.RESUME: self._commands.resume,
                CommandName.CONTEXT: self._context_command,
                CommandName.COMPACT: self._compact_command,
                CommandName.AUTH: self._provider_configuration.auth_command,
                CommandName.MODEL: self._model_command,
                CommandName.THINKING: self._commands.thinking,
                CommandName.WORKSPACE: self._diagnostic_commands.workspace,
                CommandName.DIFF: self._change_commands.diff,
                CommandName.UNDO: self._change_commands.undo,
                CommandName.REDO: self._change_commands.redo,
                CommandName.TOOLS: self._diagnostic_commands.tools,
                CommandName.SKILLS: self._extensions.skills,
                CommandName.MCP: self._extensions.mcp,
                CommandName.MEMORY: self._extensions.memory,
                CommandName.STATUS: self._diagnostic_commands.status,
                CommandName.USAGE: self._diagnostic_commands.usage,
                CommandName.DOCTOR: self._diagnostic_commands.doctor,
                CommandName.CONFIG: self._diagnostic_commands.config,
                CommandName.PERMISSIONS: self._permission_commands.permissions,
            },
            foreground=self._foreground,
            has_pending_interaction=lambda: self._interactions.pending is not None,
            mutation_guard=self._require_runtime_consistent,
        )
        recovery_results = await self._turns.reconcile_startup()
        self._recovery_queue = [
            result
            for result in recovery_results
            if result.status
            in {RecoveryStatus.RESUMABLE, RecoveryStatus.INTERACTION_REQUIRED}
        ]
        await self._present_next_recovery()
        self._initialized = True

    async def _close_activation_candidate(self, candidate: McpManager) -> None:
        close_task = asyncio.create_task(candidate.aclose())
        try:
            done, _ = await asyncio.wait(
                (close_task,),
                timeout=_ACTIVATION_ROLLBACK_TIMEOUT_SECONDS,
            )
        except BaseException:
            close_task.cancel()
            close_task.add_done_callback(_consume_background_task_result)
            return
        if close_task not in done:
            close_task.cancel()
            close_task.add_done_callback(_consume_background_task_result)
            await asyncio.sleep(0)
            return
        with suppress(Exception, asyncio.CancelledError):
            close_task.result()

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

    def _apply_provider_configuration(
        self,
        snapshot: ProviderConfigurationSnapshot,
    ) -> None:
        sources, application_config = snapshot
        self._sources = sources
        self._application_config = application_config

    def _on_thread_selected(self) -> None:
        pending = self._interactions.pending
        if (
            pending is not None
            and pending.kind is InteractionKind.FULL_ACCESS_CONFIRMATION
        ):
            self._interactions.discard(pending.id)
        self._permission_session.reset()

    async def _prepare_turn_extensions(self) -> None:
        if self._extensions is None:
            raise RuntimeError("Application extensions are not initialized.")
        await self._extensions.prepare_turn_extensions()

    def _provider_factory(self) -> GatewayFactory:
        def build(provider: ProviderId, model: str) -> ModelGateway:
            secrets = self._sources.secrets
            retries = self._application_config.budgets.provider_retries
            if provider == "deepseek":
                secret = secrets.deepseek_api_key
                if secret is None:
                    raise AssertionError("DeepSeek credential preflight was bypassed.")
                adapter: ModelProvider = DeepSeekProvider(
                    api_key=secret.get_secret_value(),
                    model=model,
                )
            else:
                secret = secrets.moonshot_api_key
                if secret is None:
                    raise AssertionError("Kimi credential preflight was bypassed.")
                adapter = KimiProvider(
                    api_key=secret.get_secret_value(),
                    model=model,
                    region=self._application_config.providers.kimi_region,
                )
            return ModelGateway(
                {provider: adapter},
                retry_policy=RetryPolicy(max_retries=retries),
                sleeper=asyncio.sleep,
            )

        return build

    def _turn_config(self, thread: Thread) -> TurnConfig:
        selected = resolve_turn_config(
            self._application_config,
            thread=ThreadConfigState(
                model=thread.current_model,
                thinking_enabled=thread.thinking_enabled,
                skill_mode=thread.skill_mode,
            ),
            environ={},
        )
        model_context_limit = (
            ModelCatalog.from_application(self._application_config)
            .profile(selected.model)
            .context_limit
        )
        return resolve_turn_config(
            self._application_config,
            thread=ThreadConfigState(
                model=thread.current_model,
                thinking_enabled=thread.thinking_enabled,
                skill_mode=thread.skill_mode,
            ),
            environ={},
            model_context_limit=model_context_limit,
        )

    def _model_identity(self, thread: Thread) -> ModelIdentitySnapshot | None:
        try:
            model = self._turn_config(thread).model
        except ValueError:
            return None
        return ModelIdentitySnapshot.from_models(
            configured_model=model,
            effective_model=model,
        )

    def _initial_thread_model(self) -> str | None:
        selected = self._environ.get("AWESOME_MODEL")
        if selected is not None:
            return selected
        selected = self._application_config.providers.default_model
        if selected is not None:
            return selected
        try:
            return resolve_turn_config(
                self._application_config,
                thread=ThreadConfigState(),
                environ={},
            ).model
        except ValueError:
            return None

    async def _context_command(self, intent: CommandIntent) -> CommandOutcome:
        thread_id = self._selected_thread_id()
        if thread_id is None:
            return error("thread_not_found", "Select a Thread first.")
        assert self._context is not None
        return await self._context.context_command(intent, thread_id=thread_id)

    async def _compact_command(self, intent: CommandIntent) -> CommandOutcome:
        thread_id = self._selected_thread_id()
        if thread_id is None:
            return error("thread_not_found", "Select a Thread first.")
        assert self._context is not None
        thread = self._conversation.read_thread(thread_id).thread
        config = self._turn_config(thread)
        return await self._context.compact_command(
            intent,
            thread_id=thread_id,
            provider=config.provider,
            model=config.model,
        )

    async def _model_command(self, intent: CommandIntent) -> CommandOutcome:
        thread_id = self._selected_thread_id()
        if thread_id is None:
            return error("thread_not_found", "Select a Thread first.")
        assert self._provider_configuration is not None
        return await self._provider_configuration.model_command(
            intent,
            thread_id=thread_id,
        )

    def _selected_thread_id(self) -> str | None:
        return self._commands.current_thread_id if self._commands is not None else None

    def _command_usage(self) -> UsageSummary | None:
        thread_id = self._selected_thread_id()
        return None if thread_id is None else self._conversation.thread_usage(thread_id)

    def _command_status_snapshot(self) -> StatusSnapshot | None:
        thread_id = self._selected_thread_id()
        if thread_id is None:
            return None
        thread = self._conversation.read_thread(thread_id).thread
        config = self._turn_config(thread)
        candidates = self._conversation.match_thread_prefix(
            self._workspace.key,
            prefix=thread_display_id(thread.id),
            limit=200,
        )
        statuses = self._mcp.statuses() if self._mcp is not None else ()
        model_identity = self._model_identity(thread)
        if model_identity is None:
            return None
        credential = (
            self._sources.provider_credentials.deepseek
            if config.provider == "deepseek"
            else self._sources.provider_credentials.kimi
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
                if self._provider_is_configured(config.provider)
                else "not_configured"
            ),
            thinking_enabled=thread.thinking_enabled,
            skill_mode=thread.skill_mode,
            local_memory_enabled=(
                self._local_memory.enabled if self._local_memory is not None else False
            ),
            mem0_enabled=(
                self._mem0_session.enabled if self._mem0_session is not None else False
            ),
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
                if self._mem0_diagnostic is not None
                and self._mem0_session is not None
                and self._mem0_session.enabled
                else 0
            ),
            permission_mode=self._permission_session.mode,
            credential_source=credential.selected_source,
            credential_source_available=credential.source_available,
            context_used_tokens=sum(
                ContextManifestItem.model_validate(item).estimated_tokens
                for item in self._conversation.latest_context_manifest(thread_id)
            ),
            context_budget_tokens=config.budgets.total_context_tokens,
            changed_file_count=self._latest_agent_change_file_count(thread_id),
        )

    def _latest_agent_change_file_count(self, thread_id: str) -> int:
        if self._change_store is None:
            return 0
        activities = self._conversation.read_thread(thread_id).tool_activities
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
            change_set = self._change_store.get(identifier)
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

    def _mem0_identity(self) -> Mem0Identity | None:
        user_id = self._application_config.memory.mem0_user_id
        if user_id is None:
            return None
        return Mem0Identity(user_id=user_id, workspace_key=self._workspace.key)

    def _create_mem0_adapter(self) -> Mem0CloudAdapter | None:
        client = self._injected_mem0_client
        if client is None:
            secret = self._sources.secrets.mem0_api_key
            try:
                client = create_mem0_client(
                    secret.get_secret_value() if secret is not None else None
                )
            except Mem0CloudError as error:
                self._mem0_diagnostic = error.diagnostic
                return None
        return Mem0CloudAdapter(cast(Mem0Client, client))

    def _seal_turn(self, turn_id: str) -> None:
        if self._change_scope is not None:
            self._change_scope.seal(turn_id)

    def _seal_direct(self, operation_id: str) -> None:
        if self._change_scope is not None:
            self._change_scope.seal(operation_id)

    async def _require_runtime_consistent(self) -> None:
        assert self._provider_configuration is not None
        try:
            await self._provider_configuration.ensure_consistent()
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

    def _require_active(self) -> None:
        self._require_open()
        if not self._initialized:
            raise _application_failure(
                ProductErrorCode.WORKSPACE_NOT_TRUSTED,
                "Trust the workspace before using project capabilities.",
            )

    def _require_open(self) -> None:
        if self._closed or self._foreground.closing:
            raise _application_failure(
                ProductErrorCode.OPERATION_BUSY,
                "Application is shutting down.",
            )

    def _require_provider_configured(self, provider: ProviderId) -> None:
        if not self._provider_is_configured(provider):
            raise _application_failure(
                ProductErrorCode.PROVIDER_NOT_CONFIGURED,
                f"{provider} credentials are not configured.",
                data={"provider": provider},
            )

    def _require_selected_thread(self, thread_id: str) -> None:
        if self._selected_thread_id() != thread_id:
            raise _application_failure(
                ProductErrorCode.INVALID_ARGUMENTS,
                "Select the target Thread before starting an operation.",
            )

    def _provider_is_configured(self, provider: ProviderId) -> bool:
        status = self._sources.secret_status
        return (
            status.deepseek_api_key
            if provider == "deepseek"
            else status.moonshot_api_key
        )

    def _workspace_presentation(
        self,
        *,
        include_branch: bool,
    ) -> WorkspacePresentation:
        return WorkspacePresentation(
            display_path=str(self._workspace.display_path),
            branch=self._workspace_branch if include_branch else None,
        )

    def _page_change_summaries(
        self,
        activities: tuple[ToolActivity, ...],
    ) -> tuple[ChangeSetSummary, ...]:
        if self._change_store is None or self._change_analyzer is None:
            return ()
        summaries: list[ChangeSetSummary] = []
        seen: set[str] = set()
        for activity in activities:
            change_set_id = activity.change_set_id
            if change_set_id is None or change_set_id in seen:
                continue
            seen.add(change_set_id)
            change_set = self._change_store.get(change_set_id)
            if change_set is None:
                continue
            analysis = self._change_analyzer.analyze(change_set_id)
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
