from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue

from awesome_agent.application.commands import (
    CommandIntent,
    CommandName,
    CommandOption,
    CommandResult,
    CommandSelection,
    CommandStatus,
)
from awesome_agent.application.dispatcher import CommandDispatcher
from awesome_agent.application.interactions import (
    InteractionCoordinator,
    InteractionDecision,
    InteractionKind,
)
from awesome_agent.application.operations import OperationController
from awesome_agent.conversation import ConversationService
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
    InteractionRequiredPayload,
    ToolResultPayload,
)
from awesome_agent.core.tools import (
    ToolError,
    ToolErrorCode,
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolExecutor,
    ToolRequest,
    ToolResult,
    ToolStatus,
)
from awesome_agent.core.tools.builtins import (
    register_modifying_tools,
    register_read_tools,
)
from awesome_agent.core.tools.command_policy import (
    InteractionRequired as ExecuteInteractionRequired,
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
from awesome_agent.paths import AwesomePaths
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore
from awesome_agent.storage.conversations import SQLiteToolActivityRepository
from awesome_agent.storage.mcp import SQLiteMcpEnablementStore
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore

type TurnSubmitter = Callable[[str, str], Awaitable[object]]

_SKILL_COMMANDS = {
    CommandName.INIT: ("init", "Initialize durable workspace guidance."),
    CommandName.REVIEW: ("review", "Review the current change."),
    CommandName.DEBUG: ("debug", "Debug the current failure from evidence."),
    CommandName.TEST: ("test", "Design and run focused validation."),
    CommandName.COMMIT: ("git-workflow", "Prepare the current change for commit."),
}


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
    ) -> None:
        self._conversation = conversation
        self._catalog = catalog
        self._loader = loader
        self._manager = manager
        self._enablements = enablements
        self._workspace_key = workspace_key
        self._registry = registry
        self._submit_turn = submit_turn

    async def handle(
        self,
        intent: CommandIntent,
        *,
        thread_id: str,
    ) -> CommandResult:
        if intent.name is CommandName.SKILLS:
            return self._skills(intent)
        if intent.name is CommandName.SKILL:
            return self._skill(intent, thread_id)
        if intent.name is CommandName.MCP:
            return await self._mcp(intent)
        if intent.name in _SKILL_COMMANDS:
            return await self._skill_command(intent, thread_id)
        return self._error("command_not_available", "Command is not available.")

    async def prepare_turn_extensions(self) -> None:
        await self._manager.start_enabled()
        self._synchronize_registry()

    def _skills(self, intent: CommandIntent) -> CommandResult:
        if intent.arguments:
            return self._error("invalid_arguments", "Usage: /skills")
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
                {"effective": effective, "diagnostics": diagnostics},
            ),
        )

    def _skill(self, intent: CommandIntent, thread_id: str) -> CommandResult:
        current = self._conversation.read_thread(thread_id).thread.skill_mode
        if not intent.arguments:
            options = (
                CommandOption(
                    value="auto",
                    label="Auto",
                    selected=current == "auto",
                ),
                CommandOption(
                    value="off",
                    label="Off",
                    selected=current == "off",
                ),
                *tuple(
                    CommandOption(
                        value=item.name,
                        label=item.name,
                        description=item.description,
                        selected=current == item.name,
                    )
                    for item in self._catalog.descriptors()
                ),
            )
            return CommandResult(
                status=CommandStatus.SUCCESS,
                data={"skill_mode": current},
                selection=CommandSelection(
                    prompt="Select the Skill mode for future Turns.",
                    options=options,
                ),
            )
        if len(intent.arguments) != 1:
            return self._error(
                "invalid_arguments",
                "Usage: /skill [auto|off|name]",
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
        accepted = await self._submit_turn(thread_id, content)
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
                choices=(InteractionDecision.TRUST, InteractionDecision.DENY),
                scope=None,
            )
            await self._emitter.emit(
                InteractionRequiredPayload(
                    interaction_id=pending.id,
                    interaction_kind="workspace_trust",
                    prompt=pending.prompt,
                    choices=tuple(choice.value for choice in pending.choices),
                )
            )
        return StartupResult(
            status=StartupStatus.TRUST_REQUIRED,
            session_id=self._session_id,
            interaction_id=pending.id,
        )

    def _activate_trusted_services(self) -> None:
        store = SQLiteChangeSetStore(self._paths.application_db)
        blobs = FileChangeBlobStore(self._paths.state_dir / "change-journal")
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
        if pending.kind is InteractionKind.EXECUTE_BOUNDARY:
            if decision is InteractionDecision.TRUST:
                raise ValueError("trust is invalid for execute interactions.")
            self._interactions.resolve(interaction_id, decision)
            return None
        if decision is InteractionDecision.ALLOW_ONCE:
            raise ValueError("allow_once is invalid for workspace trust.")
        if not self._interactions.resolve(interaction_id, decision):
            return None
        resolved = await self._interactions.wait(interaction_id)
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
            )
            try:
                try:
                    result = await executor.execute(request, context=context)
                except ExecuteInteractionRequired as interaction:
                    pending = self._interactions.create(
                        kind=InteractionKind.EXECUTE_BOUNDARY,
                        prompt=interaction.prompt,
                        choices=(
                            InteractionDecision.ALLOW_ONCE,
                            InteractionDecision.DENY,
                        ),
                        scope=interaction.scope,
                    )
                    await self._emitter.emit(
                        InteractionRequiredPayload(
                            interaction_id=pending.id,
                            interaction_kind="execute_boundary",
                            prompt=pending.prompt,
                            choices=tuple(choice.value for choice in pending.choices),
                        ),
                        turn_id=turn_id,
                    )
                    decision = await self._interactions.wait(pending.id)
                    if decision is InteractionDecision.DENY:
                        result = await self._tool_error(
                            request,
                            turn_id,
                            ToolErrorCode.PERMISSION_DENIED,
                            "Command execution was denied.",
                        )
                    else:
                        result = await executor.execute(
                            request,
                            context=replace(
                                context,
                                allowed_interaction_scopes=frozenset(
                                    {interaction.scope}
                                ),
                            ),
                        )
                return result
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
        result = ToolResult(
            call_id=request.call_id,
            tool_name=request.tool_name,
            status=ToolStatus.ERROR,
            content=message,
            error=ToolError(code=code, message=message),
        )
        await self._emitter.emit(
            ToolResultPayload(
                kind=EventType.TOOL_FAILED,
                call_id=request.call_id,
                tool_name=request.tool_name,
                summary=message,
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
