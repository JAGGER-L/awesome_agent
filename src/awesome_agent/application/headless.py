from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue

from awesome_agent.application.commands import (
    COMMAND_OWNERS,
    CommandIntent,
    CommandName,
    CommandOption,
    CommandOwner,
    CommandResult,
    CommandSelection,
    CommandStatus,
)
from awesome_agent.application.dispatcher import CommandDispatcher
from awesome_agent.application.interactions import (
    InteractionCoordinator,
    InteractionDecision,
    InteractionKind,
    tool_approval_choices,
    workspace_trust_choices,
)
from awesome_agent.application.operations import OperationController
from awesome_agent.config import UserConfigDocument, UserConfigWriter
from awesome_agent.conversation import ConversationService, Thread, ThreadNotFound
from awesome_agent.core.changes import (
    ChangeJournal,
    ChangeLifecycle,
    ChangeOperations,
)
from awesome_agent.core.changes.errors import (
    ChangeBlobCorrupt,
    ChangeConflict,
    ChangeLifecycleError,
    ChangeNotReversible,
    ChangeSetNotFound,
    PendingMutationConflict,
)
from awesome_agent.core.contracts import new_identifier
from awesome_agent.core.events import (
    EventEmitter,
    EventSink,
    EventType,
    InteractionChoicePayload,
    InteractionRequiredPayload,
    InteractionResolvedPayload,
    ToolResultPayload,
)
from awesome_agent.core.tools import (
    ToolError,
    ToolErrorCode,
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolExecutor,
    ToolPresentation,
    ToolRequest,
    ToolResult,
    ToolStatus,
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
    McpServerStatus,
    McpSource,
    McpUnavailable,
)
from awesome_agent.extensions.mcp.adapter import McpToolAdapter
from awesome_agent.extensions.mcp.models import mcp_config_hash
from awesome_agent.extensions.skills import SkillCatalog, SkillLoader, SkillNotFound
from awesome_agent.memory import (
    CloudDeleteStatus,
    LocalMemoryService,
    Mem0CloudAdapter,
    Mem0CloudError,
    Mem0Diagnostic,
    Mem0Identity,
    MemoryMutationResult,
    MemoryMutationStatus,
    MemoryScope,
    ensure_mem0_user_id,
    refresh_local_memory_tools,
)
from awesome_agent.paths import AwesomePaths
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore
from awesome_agent.storage.conversations import SQLiteToolActivityRepository
from awesome_agent.storage.mcp import SQLiteMcpEnablementStore
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore

type TurnSubmitter = Callable[[str, str, str], Awaitable[object]]
type CommandDelegate = Callable[[CommandIntent, str], Awaitable[CommandResult]]
type Mem0StateChanged = Callable[[bool, Mem0Identity | None], None]

_SKILL_COMMANDS = {
    CommandName.INIT: ("init", "Initialize durable workspace guidance."),
}


class ConversationCommandService:
    """Own Thread selection and future-Turn conversation controls."""

    def __init__(
        self,
        *,
        conversation: ConversationService,
        workspace_key: str,
        delegate: CommandDelegate,
        default_model: Callable[[], str | None] = lambda: None,
        on_thread_selected: Callable[[], None] = lambda: None,
    ) -> None:
        self._conversation = conversation
        self._workspace_key = workspace_key
        self._delegate = delegate
        self._default_model = default_model
        self._on_thread_selected = on_thread_selected
        self._current_thread_id: str | None = None

    @property
    def current_thread_id(self) -> str | None:
        return self._current_thread_id

    async def handle(self, intent: CommandIntent) -> CommandResult:
        owner = COMMAND_OWNERS[intent.name]
        if owner is CommandOwner.INK:
            return _command_error(
                "surface_command",
                "This command is owned by the interactive surface.",
            )
        if intent.name is CommandName.NEW:
            return self._new(intent)
        if intent.name is CommandName.RESUME:
            return self._resume(intent)
        if intent.name is CommandName.THINKING:
            return self._thinking(intent)
        thread_id = self._selected_thread_id()
        if thread_id is None:
            return _command_error(
                "thread_not_found",
                "Create or resume a Thread first.",
            )
        return await self._delegate(intent, thread_id)

    def _new(self, intent: CommandIntent) -> CommandResult:
        title = " ".join(intent.arguments).strip() or None
        thread = self._conversation.create_thread(
            self._workspace_key,
            title,
            current_model=self._default_model(),
        )
        self._current_thread_id = thread.id
        self._on_thread_selected()
        return CommandResult(
            status=CommandStatus.SUCCESS,
            data={"thread_id": thread.id, "title": thread.title},
        )

    def _resume(self, intent: CommandIntent) -> CommandResult:
        if len(intent.arguments) > 1:
            return _command_error("invalid_arguments", "Usage: /resume [thread_id]")
        if not intent.arguments:
            page = self._conversation.list_thread_page(
                self._workspace_key,
                cursor=None,
                limit=200,
            )
            if not page.threads:
                return CommandResult(
                    status=CommandStatus.SUCCESS,
                    data={"threads": []},
                )
            return CommandResult(
                status=CommandStatus.SUCCESS,
                selection=CommandSelection(
                    prompt="Select a Thread to resume.",
                    options=tuple(
                        CommandOption(
                            value=thread.id,
                            label=thread.title,
                            selected=thread.id == self._current_thread_id,
                        )
                        for thread in page.threads
                    ),
                ),
            )
        requested = intent.arguments[0]
        matches: list[Thread] = []
        try:
            exact = self._conversation.read_thread(requested).thread
        except ThreadNotFound:
            exact = None
        if exact is not None and exact.workspace_key == self._workspace_key:
            matches = [exact]
        elif re.fullmatch(r"thread_[a-f0-9]{8,32}", requested):
            matches = list(
                self._conversation.match_thread_prefix(
                    self._workspace_key,
                    prefix=requested,
                    limit=200,
                )
            )
        if not matches:
            return _command_error("thread_not_found", "Thread was not found.")
        if len(matches) > 1:
            return CommandResult(
                status=CommandStatus.SUCCESS,
                selection=CommandSelection(
                    prompt="Select a matching Thread to resume.",
                    options=tuple(
                        CommandOption(value=thread.id, label=thread.title)
                        for thread in matches
                    ),
                ),
            )
        thread = matches[0]
        self._current_thread_id = thread.id
        self._on_thread_selected()
        return CommandResult(
            status=CommandStatus.SUCCESS,
            data={"thread_id": thread.id, "title": thread.title},
        )

    def _thinking(self, intent: CommandIntent) -> CommandResult:
        thread = self._selected_thread()
        if thread is None:
            return _command_error("thread_not_found", "Select a Thread first.")
        if not intent.arguments:
            return CommandResult(
                status=CommandStatus.SUCCESS,
                data={"thinking_enabled": thread.thinking_enabled},
                selection=CommandSelection(
                    prompt="Select thinking mode for future Turns.",
                    options=(
                        CommandOption(
                            value="off",
                            label="Off",
                            selected=not thread.thinking_enabled,
                        ),
                        CommandOption(
                            value="on",
                            label="On",
                            selected=thread.thinking_enabled,
                        ),
                    ),
                ),
            )
        if len(intent.arguments) != 1 or intent.arguments[0] not in {"on", "off"}:
            return _command_error(
                "invalid_arguments",
                "Usage: /thinking [on|off]",
            )
        updated = self._conversation.set_thinking(
            thread.id,
            intent.arguments[0] == "on",
        )
        return CommandResult(
            status=CommandStatus.SUCCESS,
            data={"thinking_enabled": updated.thinking_enabled},
        )

    def _selected_thread_id(self) -> str | None:
        return self._current_thread_id

    def _selected_thread(self) -> Thread | None:
        thread_id = self._selected_thread_id()
        if thread_id is None:
            return None
        return self._conversation.read_thread(thread_id).thread


def _command_error(code: str, content: str) -> CommandResult:
    return CommandResult(
        status=CommandStatus.ERROR,
        content=content,
        data={"error_code": code},
    )


class ApplicationExtensionService:
    """Headless Skill/MCP commands shared by future product surfaces."""

    def __init__(
        self,
        *,
        conversation: ConversationService,
        catalog: SkillCatalog,
        loader: SkillLoader,
        manager: McpManager,
        enablements: SQLiteMcpEnablementStore,
        workspace_key: str,
        registry: ToolRegistry,
        submit_turn: TurnSubmitter,
        local_memory: LocalMemoryService | None = None,
        config_writer: UserConfigWriter | None = None,
        mem0_cloud: Mem0CloudAdapter | None = None,
        mem0_enabled: bool = False,
        mem0_user_id: str | None = None,
        mem0_initialization_diagnostic: Mem0Diagnostic | None = None,
        mem0_state_changed: Mem0StateChanged = lambda enabled, identity: None,
        has_active_turn: Callable[[], bool] = lambda: False,
    ) -> None:
        self._conversation = conversation
        self._catalog = catalog
        self._loader = loader
        self._manager = manager
        self._enablements = enablements
        self._workspace_key = workspace_key
        self._registry = registry
        self._submit_turn = submit_turn
        self._local_memory = local_memory
        self._config_writer = config_writer
        self._mem0_cloud = mem0_cloud
        self._mem0_enabled = mem0_enabled
        self._mem0_user_id = mem0_user_id
        self._mem0_initialization_diagnostic = mem0_initialization_diagnostic
        self._mem0_identity: Mem0Identity | None = None
        self._mem0_state_changed = mem0_state_changed
        self._has_active_turn = has_active_turn

    async def handle(
        self,
        intent: CommandIntent,
        *,
        thread_id: str,
    ) -> CommandResult:
        if intent.name is CommandName.SKILLS:
            return self._skills(intent, thread_id)
        if intent.name is CommandName.MCP:
            return await self._mcp(intent)
        if intent.name is CommandName.MEMORY:
            return await self._memory(intent)
        if intent.name in _SKILL_COMMANDS:
            return await self._skill_command(intent, thread_id)
        return self._error("command_not_available", "Command is not available.")

    async def prepare_turn_extensions(self) -> None:
        await self._manager.start_enabled()
        self._synchronize_registry()

    async def _memory(self, intent: CommandIntent) -> CommandResult:
        service = self._local_memory
        arguments = intent.arguments
        if not arguments:
            return self._memory_status()
        action = arguments[0]
        if action == "mem0":
            return await self._mem0_command(arguments[1:])
        if service is None:
            return self._error(
                "command_not_available",
                "Local memory is not available in this Host.",
            )
        if action == "local" and len(arguments) == 2 and arguments[1] in {"on", "off"}:
            if self._config_writer is None:
                return self._error(
                    "command_not_available",
                    "User configuration is not writable in this Host.",
                )
            if self._has_active_turn():
                return self._error(
                    "turn_busy",
                    "Change local memory after the active Turn completes.",
                )
            enabled = arguments[1] == "on"

            def update(document: UserConfigDocument) -> UserConfigDocument:
                memory = document.memory.model_copy(
                    update={"local_file_memory": enabled}
                )
                return document.model_copy(update={"memory": memory})

            self._config_writer.update(update)
            service.set_enabled(enabled)
            refresh_local_memory_tools(self._registry, service)
            return self._memory_status()
        if action == "list" and len(arguments) == 2:
            scope = _memory_scope(arguments[1])
            if scope is None:
                return self._memory_usage_error()
            document = service.snapshot(scope)
            return CommandResult(
                status=CommandStatus.SUCCESS,
                data=cast(
                    dict[str, JsonValue],
                    {
                        "scope": scope.value,
                        "content_hash": document.content_hash,
                        "entries": [
                            {"id": item.id, "content": item.content}
                            for item in document.entries
                        ],
                    },
                ),
            )
        if action == "add" and len(arguments) >= 3:
            scope = _memory_scope(arguments[1])
            if scope is None:
                return self._memory_usage_error()
            observed = service.snapshot(scope)
            result = service.add(
                scope,
                " ".join(arguments[2:]),
                expected_hash=observed.content_hash,
            )
            return _memory_mutation_result(result)
        if action == "replace" and len(arguments) >= 4:
            scope = _memory_scope(arguments[1])
            if scope is None:
                return self._memory_usage_error()
            observed = service.snapshot(scope)
            result = service.replace(
                scope,
                arguments[2],
                " ".join(arguments[3:]),
                expected_hash=observed.content_hash,
            )
            return _memory_mutation_result(result)
        if action == "remove" and len(arguments) == 3:
            scope = _memory_scope(arguments[1])
            if scope is None:
                return self._memory_usage_error()
            observed = service.snapshot(scope)
            result = service.remove(
                scope,
                arguments[2],
                expected_hash=observed.content_hash,
            )
            return _memory_mutation_result(result)
        return self._memory_usage_error()

    async def _mem0_command(self, arguments: tuple[str, ...]) -> CommandResult:
        if arguments in {("on",), ("off",)}:
            if self._config_writer is None:
                return self._error(
                    "command_not_available",
                    "User configuration is not writable in this Host.",
                )
            if self._has_active_turn():
                return self._error(
                    "turn_busy",
                    "Change Mem0 after the active Turn completes.",
                )
            enabled = arguments[0] == "on"
            if enabled and self._mem0_cloud is None:
                diagnostic = self._mem0_initialization_diagnostic or Mem0Diagnostic(
                    code="mem0_unavailable",
                    operation="initialize",
                )
                return self._error(
                    diagnostic.code,
                    "Mem0 Cloud is not available in this Host.",
                )
            if enabled:
                user_id = ensure_mem0_user_id(self._config_writer)
                self._mem0_identity = Mem0Identity(
                    user_id=user_id,
                    workspace_key=self._workspace_key,
                )
                self._mem0_user_id = user_id

            def update(document: UserConfigDocument) -> UserConfigDocument:
                memory = document.memory.model_copy(update={"mem0_cloud": enabled})
                return document.model_copy(update={"memory": memory})

            self._config_writer.update(update)
            self._mem0_enabled = enabled
            self._mem0_state_changed(enabled, self._mem0_identity)
            return self._memory_status()

        if not self._mem0_enabled:
            return self._error(
                "memory_disabled",
                "Mem0 Cloud memory is disabled.",
            )
        adapter = self._mem0_cloud
        if adapter is None:
            diagnostic = self._mem0_initialization_diagnostic or Mem0Diagnostic(
                code="mem0_unavailable",
                operation="initialize",
            )
            return self._error(
                diagnostic.code,
                "Mem0 Cloud is not available in this Host.",
            )
        identity = self._current_mem0_identity()
        if identity is None:
            return self._error(
                "mem0_identity_missing",
                "Mem0 Cloud identity is unavailable.",
            )
        if len(arguments) >= 2 and arguments[0] == "search":
            query = " ".join(arguments[1:]).strip()
            if not query:
                return self._memory_usage_error()
            try:
                memories = await adapter.search(
                    query,
                    user_id=identity.user_id,
                    workspace_key=identity.workspace_key,
                )
            except Mem0CloudError as error:
                return self._error(
                    error.diagnostic.code,
                    "Mem0 Cloud search did not complete.",
                )
            return CommandResult(
                status=CommandStatus.SUCCESS,
                data={
                    "memories": [
                        cast(
                            JsonValue,
                            {
                                "id": memory.id,
                                "content": memory.content,
                                "scope": memory.scope.value,
                                "fact_hash": memory.fact_hash,
                            },
                        )
                        for memory in memories
                    ]
                },
            )
        if len(arguments) == 2 and arguments[0] == "remove":
            result = await adapter.remove_scoped(arguments[1], identity)
            successful = result.status is CloudDeleteStatus.REMOVED
            return CommandResult(
                status=CommandStatus.SUCCESS if successful else CommandStatus.ERROR,
                content=(
                    "Cloud memory removed."
                    if successful
                    else "Cloud memory was not removed."
                ),
                data={
                    "status": result.status.value,
                    "memory_id": result.memory_id,
                    "error_code": (
                        result.diagnostic.code
                        if result.diagnostic is not None
                        else None
                    ),
                },
            )
        return self._memory_usage_error()

    def _current_mem0_identity(self) -> Mem0Identity | None:
        if self._mem0_identity is not None:
            return self._mem0_identity
        if self._mem0_user_id is None:
            return None
        self._mem0_identity = Mem0Identity(
            user_id=self._mem0_user_id,
            workspace_key=self._workspace_key,
        )
        return self._mem0_identity

    def _memory_status(self) -> CommandResult:
        service = self._local_memory
        local: JsonValue = (
            cast(JsonValue, service.status().model_dump(mode="json"))
            if service is not None
            else {"available": False, "enabled": False}
        )
        mem0: dict[str, JsonValue] = {
            "available": self._mem0_cloud is not None,
            "enabled": self._mem0_enabled,
        }
        if self._mem0_initialization_diagnostic is not None:
            mem0["error_code"] = self._mem0_initialization_diagnostic.code
        return CommandResult(
            status=CommandStatus.SUCCESS,
            data={
                "local": local,
                "mem0": mem0,
            },
        )

    def _memory_usage_error(self) -> CommandResult:
        return self._error(
            "invalid_arguments",
            "Usage: /memory [local on|off|list <scope>|add <scope> <content>|"
            "replace <scope> <id> <content>|remove <scope> <id>|mem0 on|off|"
            "search <query>|remove <id>]",
        )

    def _skills(self, intent: CommandIntent, thread_id: str) -> CommandResult:
        if intent.arguments:
            return self._select_skill(intent, thread_id)
        current = self._conversation.read_thread(thread_id).thread.skill_mode
        effective = [
            {
                "name": item.name,
                "description": item.description,
                "source": item.source.value,
            }
            for item in self._catalog.descriptors()
        ]
        diagnostics = [
            {
                "code": item.code,
                "name": item.name,
                "source": item.source.value,
                "message": item.message,
            }
            for item in self._catalog.diagnostics()
        ]
        return CommandResult(
            status=CommandStatus.SUCCESS,
            data=cast(
                dict[str, JsonValue],
                {
                    "effective": effective,
                    "diagnostics": diagnostics,
                    "skill_mode": current,
                },
            ),
            selection=CommandSelection(
                prompt="Select the Skill mode for future Turns.",
                options=(
                    CommandOption(
                        value="auto", label="Auto", selected=current == "auto"
                    ),
                    CommandOption(value="off", label="Off", selected=current == "off"),
                    *tuple(
                        CommandOption(
                            value=item.name,
                            label=item.name,
                            description=item.description,
                            selected=current == item.name,
                        )
                        for item in self._catalog.descriptors()
                    ),
                ),
            ),
        )

    def _select_skill(self, intent: CommandIntent, thread_id: str) -> CommandResult:
        if len(intent.arguments) != 1:
            return self._error(
                "invalid_arguments",
                "Usage: /skills [auto|off|name]",
            )
        selection = intent.arguments[0]
        if selection not in {"auto", "off"}:
            try:
                self._catalog.resolve(selection)
            except SkillNotFound:
                return self._error("skill_not_found", "Skill was not found.")
        updated = self._conversation.set_skill_mode(thread_id, selection)
        return CommandResult(
            status=CommandStatus.SUCCESS,
            data={"skill_mode": updated.skill_mode},
        )

    async def _skill_command(
        self,
        intent: CommandIntent,
        thread_id: str,
    ) -> CommandResult:
        skill_name, prompt = _SKILL_COMMANDS[intent.name]
        try:
            self._catalog.resolve(skill_name)
        except SkillNotFound:
            return self._error("skill_not_found", "Bundled Skill is unavailable.")
        self._conversation.set_skill_mode(thread_id, skill_name)
        content = " ".join((prompt, *intent.arguments)).strip()
        accepted = await self._submit_turn(
            thread_id,
            content,
            new_identifier("client"),
        )
        return CommandResult(
            status=CommandStatus.SUCCESS,
            content="Agent Turn submitted.",
            data=_accepted_data(accepted, skill_name),
        )

    async def _mcp(self, intent: CommandIntent) -> CommandResult:
        arguments = intent.arguments
        if not arguments:
            return self._mcp_status(None)
        action = arguments[0]
        if action == "status" and len(arguments) in {1, 2}:
            return self._mcp_status(arguments[1] if len(arguments) == 2 else None)
        if action not in {"enable", "disable", "restart"} or len(arguments) != 2:
            return self._error(
                "invalid_arguments",
                "Usage: /mcp [status [id]|enable <id>|disable <id>|restart <id>]",
            )
        server_id = arguments[1]
        try:
            config = self._manager.config(server_id)
        except McpUnavailable:
            return self._error("mcp_server_not_found", "MCP server was not found.")
        if action in {"enable", "disable"} and config.source is McpSource.USER:
            return self._error(
                "user_config_required",
                "User MCP enablement is controlled by user configuration.",
            )
        if action == "enable":
            self._enablements.enable(
                self._workspace_key,
                config.id,
                mcp_config_hash(config),
            )
            status = await self._manager.refresh_enablement(config.id)
        elif action == "disable":
            self._enablements.disable(self._workspace_key, config.id)
            status = await self._manager.refresh_enablement(config.id)
            McpToolAdapter(self._manager, config.id).remove_registry_tools(
                self._registry
            )
        else:
            status = await self._manager.restart(config.id)
            self._synchronize_server(config.id)
        return CommandResult(
            status=(
                CommandStatus.SUCCESS
                if status.state is not McpConnectionState.ERROR
                else CommandStatus.ERROR
            ),
            data=_mcp_status_payload(status),
        )

    def _mcp_status(self, server_id: str | None) -> CommandResult:
        statuses: tuple[McpServerStatus, ...]
        if server_id is not None:
            try:
                statuses = (self._manager.status(server_id),)
            except McpUnavailable:
                return self._error("mcp_server_not_found", "MCP server was not found.")
        else:
            statuses = self._manager.statuses()
        return CommandResult(
            status=CommandStatus.SUCCESS,
            data=cast(
                dict[str, JsonValue],
                {"servers": [_mcp_status_payload(item) for item in statuses]},
            ),
        )

    def _synchronize_registry(self) -> None:
        for config in self._manager.configs():
            self._synchronize_server(config.id)

    def _synchronize_server(self, server_id: str) -> None:
        adapter = McpToolAdapter(self._manager, server_id)
        status = self._manager.status(server_id)
        if status.connected:
            adapter.replace_registry_tools(
                self._registry, self._manager.tools(server_id)
            )
        else:
            adapter.remove_registry_tools(self._registry)

    def _error(self, code: str, content: str) -> CommandResult:
        return CommandResult(
            status=CommandStatus.ERROR,
            content=content,
            data={"error_code": code},
        )


def _mcp_status_payload(status: McpServerStatus) -> dict[str, JsonValue]:
    return {
        "server_id": status.server_id,
        "state": status.state.value,
        "detail": status.detail,
    }


def _accepted_data(value: object, skill_name: str) -> dict[str, JsonValue]:
    if isinstance(value, BaseModel):
        data = cast(dict[str, JsonValue], value.model_dump(mode="json"))
    elif isinstance(value, dict):
        data = cast(dict[str, JsonValue], value)
    else:
        data = {}
    return {"skill": skill_name, **data}


def _memory_scope(value: str) -> MemoryScope | None:
    try:
        return MemoryScope(value)
    except ValueError:
        return None


def _memory_mutation_result(result: MemoryMutationResult) -> CommandResult:
    successful = result.status in {
        MemoryMutationStatus.ADDED,
        MemoryMutationStatus.REPLACED,
        MemoryMutationStatus.REMOVED,
    }
    return CommandResult(
        status=CommandStatus.SUCCESS if successful else CommandStatus.ERROR,
        content=(
            "Local memory updated." if successful else "Local memory was not updated."
        ),
        data={
            "status": result.status.value,
            "scope": result.scope.value,
            "entry_id": result.entry_id,
            "content_hash": result.content_hash,
            "error_code": result.error_code,
        },
    )


class StartupStatus(StrEnum):
    READY = "ready"
    TRUST_REQUIRED = "trust_required"


class StartupResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: StartupStatus
    session_id: str
    interaction_id: str | None = None


class LocalApplication:
    def __init__(
        self,
        *,
        paths: AwesomePaths,
        workspace: WorkspaceIdentity,
        event_sink: EventSink,
    ) -> None:
        self._paths = paths
        self._workspace = workspace
        self._session_id = new_identifier("session")
        self._emitter = EventEmitter(
            session_id=self._session_id,
            workspace_key=workspace.key,
            sink=event_sink,
        )
        self._interactions = InteractionCoordinator()
        self._operations = OperationController(self._emitter)
        self._activity_writer = SQLiteToolActivityRepository(paths.application_db)
        self._trust = WorkspaceTrustService(
            SQLiteWorkspaceTrustStore(paths.application_db)
        )
        self._journal: ChangeJournal | None = None
        self._change_operations: ChangeOperations | None = None
        self._change_store: SQLiteChangeSetStore | None = None
        self._registry: ToolRegistry | None = None
        self._executor: ToolExecutor | None = None
        self._dispatcher: CommandDispatcher | None = None
        self._recovery_error: str | None = None
        self._open_change_set_id: str | None = None
        self._open_turn_id: str | None = None
        self._permission_session = PermissionSession()

    @classmethod
    def create(
        cls,
        *,
        home: Path,
        workspace: Path,
        event_sink: EventSink,
    ) -> LocalApplication:
        return cls(
            paths=AwesomePaths.from_home(home),
            workspace=resolve_workspace(workspace),
            event_sink=event_sink,
        )

    async def start(self) -> StartupResult:
        if self._executor is not None:
            return StartupResult(
                status=StartupStatus.READY,
                session_id=self._session_id,
            )
        if self._trust.status(self._workspace) is TrustStatus.TRUSTED:
            self._activate_trusted_services()
            return StartupResult(
                status=StartupStatus.READY,
                session_id=self._session_id,
            )
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
                            decision=choice.decision.value,
                            label=choice.label,
                            description=choice.description,
                        )
                        for choice in pending.choices
                    ),
                )
            )
        return StartupResult(
            status=StartupStatus.TRUST_REQUIRED,
            session_id=self._session_id,
            interaction_id=pending.id,
        )

    def _activate_trusted_services(self) -> None:
        store = SQLiteChangeSetStore(self._paths.application_db)
        blobs = FileChangeBlobStore(self._paths.change_journal_dir)
        journal = ChangeJournal(store, blobs, self._workspace)
        try:
            journal.reconcile_pending()
        except (
            ChangeBlobCorrupt,
            ChangeLifecycleError,
            PendingMutationConflict,
        ) as error:
            self._recovery_error = type(error).__name__
        registry = ToolRegistry()
        register_read_tools(registry)
        register_modifying_tools(registry, journal, ProcessRunner())
        self._journal = journal
        self._change_operations = ChangeOperations(store, blobs, self._workspace)
        self._change_store = store
        self._registry = registry
        self._executor = ToolExecutor(registry)
        self._dispatcher = self._create_dispatcher()

    async def respond(
        self,
        interaction_id: str,
        decision: InteractionDecision,
    ) -> StartupResult | None:
        pending = self._interactions.pending
        if pending is None or pending.id != interaction_id:
            return None
        if pending.kind is InteractionKind.TOOL_APPROVAL:
            if decision is InteractionDecision.TRUST:
                raise ValueError("trust is invalid for execute interactions.")
            self._interactions.resolve(interaction_id, decision)
            await self._emitter.emit(
                InteractionResolvedPayload(
                    interaction_id=interaction_id,
                    decision=decision.value,
                )
            )
            return None
        if decision in {
            InteractionDecision.ALLOW_ONCE,
            InteractionDecision.ALLOW_THREAD_WRITES,
            InteractionDecision.ENABLE_FULL_ACCESS,
        }:
            raise ValueError("allow_once is invalid for workspace trust.")
        if not self._interactions.resolve(interaction_id, decision):
            return None
        resolved = await self._interactions.wait(interaction_id)
        await self._emitter.emit(
            InteractionResolvedPayload(
                interaction_id=interaction_id,
                decision=resolved.value,
            )
        )
        if resolved is InteractionDecision.DENY:
            return None
        self._trust.accept(self._workspace)
        self._activate_trusted_services()
        return StartupResult(
            status=StartupStatus.READY,
            session_id=self._session_id,
        )

    def _workspace_not_trusted(self, request: ToolRequest) -> ToolResult:
        message = "Workspace trust is required before tools are available."
        return ToolResult(
            call_id=request.call_id,
            tool_name=request.tool_name,
            status=ToolStatus.ERROR,
            content=message,
            error=ToolError(
                code=ToolErrorCode.WORKSPACE_NOT_TRUSTED,
                message=message,
            ),
        )

    async def execute_tool(
        self,
        request: ToolRequest,
        *,
        turn_id: str | None = None,
    ) -> ToolResult:
        if self._executor is None:
            return self._workspace_not_trusted(request)
        executor = self._executor
        assert self._registry is not None
        registered = self._registry.resolve(request.tool_name)
        modifying = registered is not None and not registered.spec.read_only

        async def run(operation_id: str) -> ToolResult:
            change_set_id = None
            if modifying:
                if self._recovery_error is not None:
                    return await self._tool_error(
                        request,
                        turn_id,
                        ToolErrorCode.CONFLICT,
                        "Change journal recovery requires attention.",
                    )
                change_set_id = self._change_set_for_turn(turn_id)

            async def resolve_tool_approval(
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
                    turn_id=turn_id,
                )
                decision = await self._interactions.wait(pending.id)
                return ToolApprovalDecision(decision.value)

            permission_session = (
                self._permission_session
                if turn_id is not None
                else PermissionSession(mode=PermissionMode.FULL_ACCESS)
            )
            context = ToolExecutionContext(
                workspace=self._workspace,
                thread_id=turn_id or self._session_id,
                operation_id=operation_id,
                turn_id=turn_id,
                origin=(
                    ToolExecutionOrigin.AGENT
                    if turn_id is not None
                    else ToolExecutionOrigin.DIRECT
                ),
                emitter=self._emitter,
                activity_writer=self._activity_writer,
                monotonic=monotonic,
                change_set_id=change_set_id,
                permission_session=permission_session,
                approval_resolver=resolve_tool_approval,
            )
            try:
                return await executor.execute(request, context=context)
            finally:
                if modifying and turn_id is None:
                    self._seal_open_change_set()

        return await self._operations.run(run, turn_id=turn_id)

    async def _tool_error(
        self,
        request: ToolRequest,
        turn_id: str | None,
        code: ToolErrorCode,
        message: str,
    ) -> ToolResult:
        assert self._registry is not None
        registered = self._registry.resolve(request.tool_name)
        configured_verb = (
            registered.spec.display_metadata.get("verb") if registered else None
        )
        verb = (
            configured_verb
            if isinstance(configured_verb, str) and configured_verb
            else request.tool_name
        )
        presentation = ToolPresentation(
            verb=verb,
            outcome="Failed",
            summary=message,
            duration_ms=0,
        )
        result = ToolResult(
            call_id=request.call_id,
            tool_name=request.tool_name,
            status=ToolStatus.ERROR,
            content=message,
            error=ToolError(code=code, message=message),
            presentation=presentation,
        )
        await self._emitter.emit(
            ToolResultPayload(
                kind=EventType.TOOL_FAILED,
                call_id=request.call_id,
                tool_name=request.tool_name,
                verb=presentation.verb,
                outcome=presentation.outcome or "Failed",
                summary=message,
                duration_ms=0,
                error_code=code.value,
            ),
            turn_id=turn_id,
        )
        return result

    def _change_set_for_turn(self, turn_id: str | None) -> str:
        assert self._journal is not None
        if self._open_change_set_id is not None:
            if turn_id is not None and turn_id == self._open_turn_id:
                return self._open_change_set_id
            self._seal_open_change_set()
        change_set = self._journal.begin(
            session_id=self._session_id,
            turn_id=turn_id,
            workspace=self._workspace,
        )
        self._open_change_set_id = change_set.id
        self._open_turn_id = turn_id
        return change_set.id

    def _seal_open_change_set(self) -> None:
        change_set_id = self._open_change_set_id
        if change_set_id is None or self._journal is None:
            return
        assert self._change_store is not None
        change_set = self._change_store.get(change_set_id)
        if change_set is not None and change_set.lifecycle is ChangeLifecycle.OPEN:
            self._journal.seal(change_set_id)
        self._open_change_set_id = None
        self._open_turn_id = None

    async def execute_direct(self, command: str) -> ToolResult:
        return await self.execute_tool(
            ToolRequest(
                call_id=new_identifier("call"),
                tool_name="execute",
                arguments={"command": command},
            )
        )

    async def dispatch(self, intent: CommandIntent) -> CommandResult:
        if self._dispatcher is None:
            return CommandResult(
                status=CommandStatus.ERROR,
                content="Workspace trust is required.",
                data={"error_code": "workspace_not_trusted"},
            )
        return await self._dispatcher.dispatch(intent)

    def _create_dispatcher(self) -> CommandDispatcher:
        dispatcher = CommandDispatcher()
        dispatcher.register(CommandName.WORKSPACE, self._command_workspace)
        dispatcher.register(CommandName.TOOLS, self._command_tools)
        dispatcher.register(CommandName.DIFF, self._command_diff)
        dispatcher.register(CommandName.UNDO, self._command_undo)
        dispatcher.register(CommandName.REDO, self._command_redo)
        dispatcher.register(CommandName.STATUS, self._command_status)
        dispatcher.register(CommandName.DOCTOR, self._command_doctor)
        return dispatcher

    def _command_error(self, code: str, content: str) -> CommandResult:
        return CommandResult(
            status=CommandStatus.ERROR,
            content=content,
            data={"error_code": code},
        )

    async def _command_workspace(self, intent: CommandIntent) -> CommandResult:
        if intent.arguments == ("revoke",):
            self._seal_open_change_set()
            self._trust.revoke(self._workspace)
            self._deactivate_services()
            return CommandResult(
                status=CommandStatus.SUCCESS,
                content="Workspace trust was revoked.",
                data={"trust": TrustStatus.UNKNOWN.value},
            )
        if intent.arguments:
            return self._command_error(
                "invalid_arguments",
                "Usage: /workspace [revoke]",
            )
        return CommandResult(
            status=CommandStatus.SUCCESS,
            content=str(self._workspace.display_path),
            data={
                "workspace_key": self._workspace.key,
                "trust": self._trust.status(self._workspace).value,
            },
        )

    async def _command_tools(self, intent: CommandIntent) -> CommandResult:
        if intent.arguments:
            return self._command_error("invalid_arguments", "Usage: /tools")
        assert self._registry is not None
        tools = [
            {
                "name": spec.name,
                "description": spec.description,
                "read_only": spec.read_only,
            }
            for spec in self._registry.specifications()
        ]
        return CommandResult(
            status=CommandStatus.SUCCESS,
            data={"tools": cast(JsonValue, tools)},
        )

    def _selected_change_set_id(self, intent: CommandIntent) -> str | None:
        self._seal_open_change_set()
        if len(intent.arguments) > 1:
            return None
        if intent.arguments:
            return intent.arguments[0]
        assert self._change_store is not None
        latest = self._change_store.latest(self._workspace.key)
        return latest.id if latest is not None else None

    async def _command_diff(self, intent: CommandIntent) -> CommandResult:
        change_set_id = self._selected_change_set_id(intent)
        if change_set_id is None:
            return self._command_error("change_set_not_found", "No ChangeSet exists.")
        assert self._change_operations is not None
        try:
            content = self._change_operations.diff(change_set_id)
        except ChangeSetNotFound:
            return self._command_error(
                "change_set_not_found", "ChangeSet was not found."
            )
        return CommandResult(
            status=CommandStatus.SUCCESS,
            content=content,
            data={"change_set_id": change_set_id},
        )

    async def _command_undo(self, intent: CommandIntent) -> CommandResult:
        return self._change_operation_command(intent, undo=True)

    async def _command_redo(self, intent: CommandIntent) -> CommandResult:
        return self._change_operation_command(intent, undo=False)

    def _change_operation_command(
        self,
        intent: CommandIntent,
        *,
        undo: bool,
    ) -> CommandResult:
        change_set_id = self._selected_change_set_id(intent)
        if change_set_id is None:
            return self._command_error("change_set_not_found", "No ChangeSet exists.")
        assert self._change_operations is not None
        try:
            result = (
                self._change_operations.undo(change_set_id)
                if undo
                else self._change_operations.redo(change_set_id)
            )
        except ChangeSetNotFound:
            return self._command_error(
                "change_set_not_found", "ChangeSet was not found."
            )
        except ChangeConflict:
            return self._command_error(
                "change_conflict",
                "Workspace content conflicts with the ChangeSet.",
            )
        except ChangeNotReversible:
            return self._command_error(
                "change_not_reversible",
                "ChangeSet has no controlled effects to restore.",
            )
        except ChangeLifecycleError:
            return self._command_error(
                "invalid_change_lifecycle",
                "ChangeSet is not in the required lifecycle.",
            )
        return CommandResult(
            status=CommandStatus.SUCCESS,
            content="ChangeSet updated.",
            data=cast(dict[str, JsonValue], result.model_dump(mode="json")),
        )

    async def _command_status(self, intent: CommandIntent) -> CommandResult:
        if intent.arguments:
            return self._command_error("invalid_arguments", "Usage: /status")
        pending = self._interactions.pending
        return CommandResult(
            status=CommandStatus.SUCCESS,
            data={
                "session_id": self._session_id,
                "workspace_key": self._workspace.key,
                "trust": self._trust.status(self._workspace).value,
                "active_operation_id": self._operations.active_operation_id,
                "pending_interaction_id": pending.id if pending is not None else None,
            },
        )

    async def _command_doctor(self, intent: CommandIntent) -> CommandResult:
        if intent.arguments:
            return self._command_error("invalid_arguments", "Usage: /doctor")
        healthy = (
            self._recovery_error is None
            and self._executor is not None
            and self._registry is not None
        )
        return CommandResult(
            status=CommandStatus.SUCCESS if healthy else CommandStatus.ERROR,
            content="Local foundation is ready."
            if healthy
            else "Recovery is required.",
            data={
                "healthy": healthy,
                "application_database": self._paths.application_db.is_file(),
                "checkpoint_parent": self._paths.checkpoint_db.parent.is_dir(),
                "tool_count": (
                    len(self._registry.specifications())
                    if self._registry is not None
                    else 0
                ),
                "recovery_error": self._recovery_error,
            },
        )

    async def cancel(self, operation_id: str) -> bool:
        return await self._operations.cancel(operation_id)

    async def close(self) -> None:
        pending = self._interactions.pending
        if pending is not None:
            self._interactions.cancel_pending()
            await self._interactions.wait(pending.id)
        await self._operations.shutdown()
        self._seal_open_change_set()
        self._deactivate_services()

    def _deactivate_services(self) -> None:
        self._journal = None
        self._change_operations = None
        self._change_store = None
        self._registry = None
        self._executor = None
        self._dispatcher = None
        self._open_change_set_id = None
        self._open_turn_id = None
