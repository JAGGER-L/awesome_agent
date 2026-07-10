from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AsyncExitStack
from pathlib import Path
from time import monotonic
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver

from awesome_agent.agent import (
    AgentRuntimeContext,
    CloudPostAnswerMemory,
    DisabledPostAnswerMemory,
    PostAnswerMemory,
    TurnBudget,
    compile_agent_graph,
)
from awesome_agent.application.commands import (
    CommandIntent,
    CommandName,
    CommandResult,
    CommandStatus,
)
from awesome_agent.application.context import ApplicationContextService
from awesome_agent.application.contracts import (
    ApplicationState,
    CancelResult,
    InitializeResult,
    InitializeStatus,
    InteractionResult,
    OperationAccepted,
    ThreadListResult,
    ThreadReadResult,
)
from awesome_agent.application.direct import DirectCommandService
from awesome_agent.application.events import ApplicationEventProjector
from awesome_agent.application.facade import LocalApplication
from awesome_agent.application.headless import (
    ApplicationExtensionService,
    ConversationCommandService,
)
from awesome_agent.application.interactions import (
    InteractionCoordinator,
    InteractionDecision,
    InteractionKind,
)
from awesome_agent.application.operations import OperationController
from awesome_agent.application.turns import TurnCoordinator
from awesome_agent.config import (
    ApplicationConfig,
    LoadedConfigSources,
    ThreadConfigState,
    TurnConfig,
    UserConfigWriter,
    load_config_sources,
    resolve_application_config,
    resolve_turn_config,
)
from awesome_agent.context import (
    ContextBuilder,
    Mem0ContextResult,
    ThreadCompressor,
    mem0_context_source,
)
from awesome_agent.conversation import ConversationService, Thread, Turn, UsageSummary
from awesome_agent.core.changes import (
    ChangeJournal,
    ChangeLifecycle,
    ChangeOperations,
)
from awesome_agent.core.contracts import new_identifier
from awesome_agent.core.events import (
    EventEmitter,
    EventSink,
    InteractionRequiredPayload,
)
from awesome_agent.core.tools import (
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolExecutor,
)
from awesome_agent.core.tools.builtins import (
    register_modifying_tools,
    register_read_tools,
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
    ModelProvider,
    ModelRequest,
    ModelTurn,
    ProviderId,
    RetryPolicy,
    SelectedModel,
)
from awesome_agent.paths import AwesomePaths
from awesome_agent.providers import DeepSeekProvider, KimiProvider
from awesome_agent.storage import SQLiteMcpEnablementStore
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore
from awesome_agent.storage.checkpoints import (
    LangGraphCheckpointStore,
    sqlite_checkpoint_saver,
)
from awesome_agent.storage.conversations import SQLiteConversationRepositories
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore

type GatewayFactory = Callable[[ProviderId, str], ModelGateway]
type McpClientFactory = Callable[[McpServerConfig], McpClient]

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
) -> LocalApplication:
    paths = AwesomePaths.from_home(home)
    identity = resolve_workspace(workspace)
    stack = AsyncExitStack()
    saver = await stack.enter_async_context(
        sqlite_checkpoint_saver(paths.checkpoint_db)
    )
    backend = _LocalApplicationBackend(
        paths=paths,
        workspace=identity,
        event_sink=event_sink,
        saver=saver,
        resources=stack,
        environ=environ,
        gateway_factory=gateway_factory,
        mcp_client_factory=mcp_client_factory,
        mem0_client=mem0_client,
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


class _ChangeScope:
    def __init__(
        self,
        *,
        journal: ChangeJournal,
        store: SQLiteChangeSetStore,
        session_id: str,
        workspace: WorkspaceIdentity,
    ) -> None:
        self._journal = journal
        self._store = store
        self._session_id = session_id
        self._workspace = workspace
        self._identifiers: dict[str, str] = {}

    def acquire(self, owner: str, *, turn_id: str | None) -> str:
        current = self._identifiers.get(owner)
        if current is not None:
            return current
        change_set = self._journal.begin(
            session_id=self._session_id,
            turn_id=turn_id,
            workspace=self._workspace,
        )
        self._identifiers[owner] = change_set.id
        return change_set.id

    def seal(self, owner: str) -> None:
        identifier = self._identifiers.pop(owner, None)
        if identifier is None:
            return
        change_set = self._store.get(identifier)
        if change_set is not None and change_set.lifecycle is ChangeLifecycle.OPEN:
            self._journal.seal(identifier)

    def reconcile(self) -> None:
        self._journal.reconcile_pending()


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
        saver: BaseCheckpointSaver[str],
        resources: AsyncExitStack,
        environ: Mapping[str, str] | None,
        gateway_factory: GatewayFactory | None,
        mcp_client_factory: McpClientFactory | None,
        mem0_client: object | None,
    ) -> None:
        self._paths = paths
        self._workspace = workspace
        self._resources = resources
        self._environ = dict(environ or {})
        self._injected_gateway_factory = gateway_factory
        self._mcp_client_factory = mcp_client_factory
        self._injected_mem0_client = mem0_client
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
        self._saver = saver
        self._checkpoints = LangGraphCheckpointStore(saver)
        self._sources = self._load_sources(workspace_trusted=False)
        self._application_config = resolve_application_config(self._sources)
        self._initialized = False
        self._closed = False
        self._commands: ConversationCommandService | None = None
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
        self._change_scope: _ChangeScope | None = None
        self._change_operations: ChangeOperations | None = None

    async def initialize_application(self) -> InitializeResult:
        if self._initialized:
            return InitializeResult(
                status=InitializeStatus.READY,
                session_id=self._session_id,
                capabilities=_CAPABILITIES,
            )
        if self._trust.status(self._workspace) is not TrustStatus.TRUSTED:
            pending = self._interactions.pending
            if pending is None:
                pending = self._interactions.create(
                    kind=InteractionKind.WORKSPACE_TRUST,
                    prompt="Trust this workspace?",
                    choices=(InteractionDecision.TRUST, InteractionDecision.DENY),
                    scope=None,
                )
                await self._emitter.emit(
                    InteractionRequiredPayload(
                        interaction_id=pending.id,
                        interaction_kind="workspace_trust",
                        prompt=pending.prompt,
                        choices=tuple(item.value for item in pending.choices),
                    )
                )
            return InitializeResult(
                status=InitializeStatus.TRUST_REQUIRED,
                session_id=self._session_id,
                interaction_id=pending.id,
                capabilities=_CAPABILITIES,
            )
        await self._activate()
        return InitializeResult(
            status=InitializeStatus.READY,
            session_id=self._session_id,
            capabilities=_CAPABILITIES,
        )

    async def application_state(self) -> ApplicationState:
        current_id = self._commands.current_thread_id if self._commands else None
        current = (
            self._conversation.read_thread(current_id).thread
            if current_id is not None
            else None
        )
        usage = _last_usage(self._conversation, current_id)
        local_enabled = self._local_memory.enabled if self._local_memory else False
        return ApplicationState(
            initialized=self._initialized,
            session_id=self._session_id,
            workspace_key=self._workspace.key,
            workspace_trusted=(
                self._trust.status(self._workspace) is TrustStatus.TRUSTED
            ),
            current_thread_id=current_id,
            current_model=current.current_model if current else None,
            thinking_enabled=current.thinking_enabled if current else False,
            skill_mode=current.skill_mode if current else "auto",
            active_operation_id=self._operations.active_operation_id,
            pending_interaction_id=(
                self._interactions.pending.id if self._interactions.pending else None
            ),
            configuration_valid=True,
            secret_status=self._sources.secret_status,
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
                if self._mem0_diagnostic is not None
                else ()
            ),
        )

    async def workspace_threads(self) -> ThreadListResult:
        return ThreadListResult(
            threads=self._conversation.list_threads(self._workspace.key)
        )

    async def thread_state(self, thread_id: str) -> ThreadReadResult:
        view = self._conversation.read_thread(thread_id)
        if view.thread.workspace_key != self._workspace.key:
            raise LookupError(thread_id)
        return ThreadReadResult(view=view)

    async def start_turn(self, thread_id: str, content: str) -> OperationAccepted:
        self._require_active()
        assert self._turns is not None
        return await self._turns.submit_turn(thread_id, content)

    async def start_direct(self, thread_id: str, command: str) -> OperationAccepted:
        self._require_active()
        assert self._direct is not None
        return await self._direct.start(thread_id, command)

    async def run_command(self, intent: CommandIntent) -> CommandResult:
        self._require_active()
        assert self._commands is not None
        return await self._commands.handle(intent)

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
        if not self._interactions.resolve(interaction_id, parsed):
            return InteractionResult(accepted=False, status="rejected")
        resolved = await self._interactions.wait(interaction_id)
        if pending.kind is InteractionKind.WORKSPACE_TRUST:
            if resolved is not InteractionDecision.TRUST:
                return InteractionResult(accepted=True, status="denied")
            self._trust.accept(self._workspace)
            await self._activate()
        return InteractionResult(accepted=True, status="resolved")

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

    async def _activate(self) -> None:
        if self._initialized:
            return
        self._sources = self._load_sources(workspace_trusted=True)
        self._application_config = resolve_application_config(self._sources)
        gateway_factory = self._injected_gateway_factory or self._provider_factory()
        gateway_router = _GatewayRouter(gateway_factory)

        change_store = SQLiteChangeSetStore(self._paths.application_db)
        change_blobs = FileChangeBlobStore(self._paths.state_dir / "change-journal")
        journal = ChangeJournal(change_store, change_blobs, self._workspace)
        self._change_scope = _ChangeScope(
            journal=journal,
            store=change_store,
            session_id=self._session_id,
            workspace=self._workspace,
        )
        self._change_scope.reconcile()
        self._change_operations = ChangeOperations(
            change_store,
            change_blobs,
            self._workspace,
        )

        registry = ToolRegistry()
        register_read_tools(registry)
        register_modifying_tools(registry, journal, ProcessRunner())
        self._registry = registry
        executor = ToolExecutor(registry)

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
            product_instructions=(
                "You are a local-first coding agent. Use tools when evidence is needed."
            ),
            skill_loader=skill_loader,
            local_memory=self._local_memory,
            mem0_recall=self._mem0_session.recall,
        )
        self._context = context_service

        graph = compile_agent_graph(self._saver)

        def runtime_factory(
            turn: Turn,
            operation_id: str,
            projector: ApplicationEventProjector,
        ) -> AgentRuntimeContext:
            turn_id = turn.id
            budgets = turn.budgets

            def tool_context(state: object) -> ToolExecutionContext:
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
                    change_set_id=self._change_scope.acquire(
                        turn_id,
                        turn_id=turn_id,
                    ),
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
            checkpoints=self._checkpoints,
            seal_changes=self._seal_turn,
            reconcile_changes=self._change_scope.reconcile,
            turn_input_preparer=context_service.prepare_turn,
        )

        def direct_context(thread_id: str, operation_id: str) -> ToolExecutionContext:
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
                change_set_id=self._change_scope.acquire(
                    operation_id,
                    turn_id=None,
                ),
            )

        self._direct = DirectCommandService(
            conversation=self._conversation,
            executor=executor,
            operations=self._operations,
            context_factory=direct_context,
            finalize_operation=self._seal_direct,
        )
        assert self._mem0_session is not None
        self._extensions = ApplicationExtensionService(
            conversation=self._conversation,
            catalog=catalog,
            loader=skill_loader,
            manager=self._mcp,
            enablements=enablements,
            workspace_key=self._workspace.key,
            registry=registry,
            submit_turn=self.start_turn,
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
        self._commands = ConversationCommandService(
            conversation=self._conversation,
            workspace_key=self._workspace.key,
            delegate=self._delegate_command,
        )
        if not self._conversation.list_threads(self._workspace.key):
            await self._commands.handle(CommandIntent(name=CommandName.NEW))
        else:
            first = self._conversation.list_threads(self._workspace.key)[0]
            await self._commands.handle(
                CommandIntent(name=CommandName.RESUME, arguments=(first.id,))
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

    def _provider_factory(self) -> GatewayFactory:
        secrets = self._sources.secrets
        retries = self._application_config.budgets.provider_retries

        def build(provider: ProviderId, model: str) -> ModelGateway:
            if provider == "deepseek":
                secret = secrets.deepseek_api_key
                if secret is None:
                    raise RuntimeError("provider_not_configured")
                adapter: ModelProvider = DeepSeekProvider(
                    api_key=secret.get_secret_value(),
                    model=model,
                )
            else:
                secret = secrets.moonshot_api_key
                if secret is None:
                    raise RuntimeError("provider_not_configured")
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

    async def _delegate_command(
        self,
        intent: CommandIntent,
        thread_id: str,
    ) -> CommandResult:
        assert self._extensions is not None
        if intent.name in {
            CommandName.SKILLS,
            CommandName.SKILL,
            CommandName.MCP,
            CommandName.MEMORY,
            CommandName.INIT,
            CommandName.REVIEW,
            CommandName.DEBUG,
            CommandName.TEST,
            CommandName.COMMIT,
        }:
            return await self._extensions.handle(intent, thread_id=thread_id)
        if intent.name is CommandName.CONTEXT:
            assert self._context is not None
            return await self._context.context_command(intent, thread_id=thread_id)
        if intent.name is CommandName.COMPACT:
            assert self._context is not None
            thread = self._conversation.read_thread(thread_id).thread
            config = self._turn_config(thread)
            return await self._context.compact_command(
                intent,
                thread_id=thread_id,
                provider=config.provider,
                model=config.model,
            )
        if intent.name is CommandName.WORKSPACE:
            if intent.arguments:
                return _error("invalid_arguments", "Usage: /workspace")
            return CommandResult(
                status=CommandStatus.SUCCESS,
                data={
                    "workspace_key": self._workspace.key,
                    "trust": self._trust.status(self._workspace).value,
                },
            )
        if intent.name is CommandName.TOOLS:
            assert self._registry is not None
            return CommandResult(
                status=CommandStatus.SUCCESS,
                data={
                    "tools": [
                        {
                            "name": spec.name,
                            "description": spec.description,
                            "read_only": spec.read_only,
                        }
                        for spec in self._registry.specifications()
                    ]
                },
            )
        if intent.name is CommandName.STATUS:
            return CommandResult(
                status=CommandStatus.SUCCESS,
                data=(await self.application_state()).model_dump(mode="json"),
            )
        if intent.name is CommandName.USAGE:
            usage = _last_usage(self._conversation, thread_id)
            return CommandResult(
                status=CommandStatus.SUCCESS,
                data=usage.model_dump(mode="json"),
            )
        if intent.name is CommandName.CONFIG:
            return CommandResult(
                status=CommandStatus.SUCCESS,
                data={
                    "sources": ["defaults", "user", "workspace", "environment"],
                    "secrets": self._sources.secret_status.model_dump(mode="json"),
                },
            )
        if intent.name is CommandName.DOCTOR:
            return CommandResult(
                status=CommandStatus.SUCCESS,
                data={
                    "configuration": "ok",
                    "sqlite": "ok",
                    "checkpoints": "ok",
                    "provider_configured": any(
                        self._sources.secret_status.model_dump(mode="json").values()
                    ),
                },
            )
        if intent.name in {CommandName.DIFF, CommandName.UNDO, CommandName.REDO}:
            return self._change_command(intent)
        return _error("command_not_available", "Command is not available.")

    def _change_command(self, intent: CommandIntent) -> CommandResult:
        assert self._change_operations is not None
        store = SQLiteChangeSetStore(self._paths.application_db)
        identifier = intent.arguments[0] if intent.arguments else None
        if identifier is None:
            latest = store.latest(self._workspace.key)
            identifier = latest.id if latest else None
        if identifier is None:
            return _error("change_set_not_found", "No ChangeSet exists.")
        try:
            if intent.name is CommandName.DIFF:
                return CommandResult(
                    status=CommandStatus.SUCCESS,
                    content=self._change_operations.diff(identifier),
                    data={"change_set_id": identifier},
                )
            result = (
                self._change_operations.undo(identifier)
                if intent.name is CommandName.UNDO
                else self._change_operations.redo(identifier)
            )
            return CommandResult(
                status=CommandStatus.SUCCESS,
                data={
                    "change_set_id": identifier,
                    "lifecycle": result.lifecycle.value,
                },
            )
        except Exception:
            return _error("change_operation_failed", "Change operation failed.")

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
            raise RuntimeError("workspace_not_trusted")


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


def _last_usage(
    conversation: ConversationService,
    thread_id: str | None,
) -> UsageSummary:
    if thread_id is None:
        return UsageSummary()
    turns = conversation.read_thread(thread_id).turns
    return turns[-1].usage if turns else UsageSummary()


def _error(code: str, message: str) -> CommandResult:
    return CommandResult(
        status=CommandStatus.ERROR,
        content=message,
        data={"error_code": code},
    )
