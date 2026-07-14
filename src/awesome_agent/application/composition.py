from __future__ import annotations

import asyncio
import subprocess
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AsyncExitStack
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
from awesome_agent.application.interactions import (
    InteractionCoordinator,
    InteractionDecision,
    InteractionKind,
    state_reset_choices,
    tool_approval_choices,
    workspace_trust_choices,
)
from awesome_agent.application.operations import OperationBusy, OperationController
from awesome_agent.application.permission_commands import PermissionCommandService
from awesome_agent.application.provider_configuration import (
    CredentialValidator,
    ProviderConfigurationService,
)
from awesome_agent.application.turns import TurnCoordinator
from awesome_agent.config import (
    ApplicationConfig,
    LoadedConfigSources,
    ThreadConfigState,
    TurnConfig,
    UserConfigWriter,
    UserSecretStore,
    load_config_sources,
    resolve_application_config,
    resolve_turn_config,
)
from awesome_agent.context import (
    CODING_AGENT_PRODUCT_INSTRUCTIONS,
    ContextBuilder,
    ContextManifestItem,
    Mem0ContextResult,
    ThreadCompressor,
    mem0_context_source,
)
from awesome_agent.conversation import (
    ConversationService,
    Thread,
    ThreadNotFound,
    ToolActivity,
    Turn,
    TurnBusy,
    TurnNotFound,
    UsageSummary,
)
from awesome_agent.core.changes import (
    ChangeAnalyzer,
    ChangeJournal,
    ChangeOperations,
)
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
    WorkspaceTrustService,
    resolve_workspace,
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
from awesome_agent.storage.pagination import (
    InvalidThreadCursor,
    decode_thread_cursor,
    encode_thread_cursor,
)
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore
from awesome_agent.version import PRODUCT_VERSION

type GatewayFactory = Callable[[ProviderId, str], ModelGateway]
type McpClientFactory = Callable[[McpServerConfig], McpClient]

_MAX_THREAD_RESULT_BYTES = 900_000

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
        self._operations = OperationController(self._emitter)
        self._interactions = InteractionCoordinator()
        self._trust = WorkspaceTrustService(
            SQLiteWorkspaceTrustStore(paths.application_db)
        )
        self._repositories = SQLiteConversationRepositories(paths.application_db)
        self._conversation = ConversationService(store=self._repositories)
        self._saver: BaseCheckpointSaver[str] | None = None
        self._checkpoints: LangGraphCheckpointStore | None = None
        self._sources = self._load_sources(workspace_trusted=False)
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
        self._permission_session = PermissionSession()
        self._state_lease: StateLease | None = None
        self._bootstrap_lock = asyncio.Lock()
        self._resources.callback(self._close_state_lease)

    async def initialize_application(self) -> InitializeResult:
        if self._initialized:
            return InitializeResult(
                product_version=PRODUCT_VERSION,
                protocol_version=2,
                status=InitializeStatus.READY,
                session_id=self._session_id,
                workspace=self._workspace_presentation(include_branch=True),
                capabilities=_CAPABILITIES,
            )
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
                protocol_version=2,
                status=InitializeStatus.TRUST_REQUIRED,
                session_id=self._session_id,
                interaction_id=pending.id,
                workspace=self._workspace_presentation(include_branch=False),
                capabilities=_CAPABILITIES,
            )
        await self._activate()
        return InitializeResult(
            product_version=PRODUCT_VERSION,
            protocol_version=2,
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
            protocol_version=2,
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
        current_id = self._commands.current_thread_id if self._commands else None
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
            workspace_trusted=(
                self._trust.status(self._workspace) is TrustStatus.TRUSTED
            ),
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
        assert self._turns is not None
        if not content.strip():
            raise _application_failure(
                ProductErrorCode.INVALID_ARGUMENTS,
                "Turn input is invalid.",
            )
        try:
            thread = self._conversation.read_thread(thread_id).thread
            config = self._turn_config(thread)
            self._require_provider_configured(config.provider)
            return await self._turns.submit_turn(
                thread_id,
                content,
                client_message_id=client_message_id,
            )
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
        assert self._direct is not None
        if not command.strip():
            raise _application_failure(
                ProductErrorCode.INVALID_ARGUMENTS,
                "Direct command is invalid.",
            )
        try:
            return await self._direct.start(thread_id, command)
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
        return await self._provider_configuration.set_credential(request)

    async def resolve_interaction(
        self,
        interaction_id: str,
        decision: str,
    ) -> InteractionResult:
        pending = self._interactions.pending
        if pending is None or pending.id != interaction_id:
            return InteractionResult(accepted=False, status="not_found")
        try:
            parsed = InteractionDecision(decision)
        except ValueError:
            return InteractionResult(accepted=False, status="invalid_decision")
        if pending.kind is InteractionKind.STATE_RESET:
            return await self._resolve_state_reset_interaction(
                interaction_id,
                parsed,
            )
        if not self._interactions.resolve(interaction_id, parsed):
            return InteractionResult(accepted=False, status="rejected")
        resolved = await self._interactions.wait(interaction_id)
        await self._emitter.emit(
            InteractionResolvedPayload(
                interaction_id=interaction_id,
                decision=resolved.value,
            ),
        )
        if pending.kind is InteractionKind.WORKSPACE_TRUST:
            if resolved is not InteractionDecision.TRUST:
                return InteractionResult(accepted=True, status="denied")
            self._trust.accept(self._workspace)
            await self._activate()
        elif pending.kind is InteractionKind.FULL_ACCESS_CONFIRMATION:
            if resolved is InteractionDecision.ENABLE_FULL_ACCESS:
                self._permission_session.mode = PermissionMode.FULL_ACCESS
                self._permission_session.granted_capabilities.clear()
            else:
                return InteractionResult(accepted=True, status="denied")
        return InteractionResult(accepted=True, status="resolved")

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

        try:
            preflight = inspect_application_state(self._paths.application_db)
            if preflight.compatibility is StateCompatibility.OLDER:
                await asyncio.to_thread(reset_local_state, self._paths, exclusive)
            elif preflight.compatibility is StateCompatibility.NEW:
                await asyncio.to_thread(
                    initialize_application_database,
                    self._paths.application_db,
                )
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
        if self._closed:
            return
        self._closed = True
        await self._operations.shutdown()
        if self._mcp is not None:
            await self._mcp.aclose()
        await self._resources.aclose()

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

    async def _activate(self) -> None:
        if self._initialized:
            return
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
            disabled=set(),
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
            )
        else:
            self._mcp = McpManager(
                configs=_mcp_configs(self._application_config),
                workspace_key=self._workspace.key,
                workspace_trusted=True,
                enablements=enablements,
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

        context_service = ApplicationContextService(
            conversation=self._conversation,
            workspace=self._workspace,
            builder=ContextBuilder(),
            compressor=ThreadCompressor(gateway_router),
            configured_total_tokens=self._application_config.budgets.total_context_tokens,
            model_context_limit=self._application_config.budgets.total_context_tokens,
            product_instructions=CODING_AGENT_PRODUCT_INSTRUCTIONS,
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
                )
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
                compressor=context_service,
                current_user_text=context_service.current_input(turn_id),
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
            on_thread_selected=self._permission_session.reset,
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
            reload_configuration=self._reload_provider_configuration,
        )
        self._diagnostic_commands = DiagnosticCommandService(
            workspace_path=self._workspace.display_path,
            registry=registry,
            permission_session=self._permission_session,
            status_reader=self._command_status_snapshot,
            usage_reader=self._command_usage,
            credential_statuses=lambda: self._sources.provider_credentials,
            provider_doctor=self._provider_configuration.doctor,
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
            }
        )
        await self._turns.reconcile_startup()
        self._initialized = True

    def _load_sources(self, *, workspace_trusted: bool) -> LoadedConfigSources:
        return load_config_sources(
            paths=self._paths,
            workspace=self._workspace.canonical_path,
            workspace_trusted=workspace_trusted,
            environ=self._environ,
        )

    def _reload_provider_configuration(self) -> None:
        self._sources = self._load_sources(workspace_trusted=True)
        self._application_config = resolve_application_config(self._sources)

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
        return resolve_turn_config(
            self._application_config,
            thread=ThreadConfigState(
                model=thread.current_model,
                thinking_enabled=thread.thinking_enabled,
                skill_mode=thread.skill_mode,
            ),
            environ={},
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
            context_budget_tokens=self._application_config.budgets.total_context_tokens,
            changed_file_count=0,
        )

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

    def _require_active(self) -> None:
        if not self._initialized:
            raise _application_failure(
                ProductErrorCode.WORKSPACE_NOT_TRUSTED,
                "Trust the workspace before using project capabilities.",
            )

    def _require_provider_configured(self, provider: ProviderId) -> None:
        if not self._provider_is_configured(provider):
            raise _application_failure(
                ProductErrorCode.PROVIDER_NOT_CONFIGURED,
                f"{provider} credentials are not configured.",
                data={"provider": provider},
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
