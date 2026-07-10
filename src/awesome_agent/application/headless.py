from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from awesome_agent.application.commands import (
    CommandIntent,
    CommandResult,
    CommandStatus,
)
from awesome_agent.application.dispatcher import CommandDispatcher
from awesome_agent.application.interactions import (
    InteractionCoordinator,
    InteractionDecision,
    InteractionKind,
)
from awesome_agent.application.operations import OperationController
from awesome_agent.core.changes import ChangeJournal, ChangeOperations
from awesome_agent.core.changes.errors import (
    ChangeBlobCorrupt,
    ChangeLifecycleError,
    PendingMutationConflict,
)
from awesome_agent.core.contracts import new_identifier
from awesome_agent.core.events import (
    EventEmitter,
    EventSink,
    InteractionRequiredPayload,
)
from awesome_agent.core.tools import (
    ToolError,
    ToolErrorCode,
    ToolExecutionContext,
    ToolExecutor,
    ToolRequest,
    ToolResult,
    ToolStatus,
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
from awesome_agent.paths import AwesomePaths
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore


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
        self._emitter = EventEmitter(session_id=self._session_id, sink=event_sink)
        self._interactions = InteractionCoordinator()
        self._operations = OperationController(self._emitter)
        self._trust = WorkspaceTrustService(
            SQLiteWorkspaceTrustStore(paths.application_db)
        )
        self._journal: ChangeJournal | None = None
        self._change_operations: ChangeOperations | None = None
        self._registry: ToolRegistry | None = None
        self._executor: ToolExecutor | None = None
        self._dispatcher: CommandDispatcher | None = None
        self._recovery_error: str | None = None

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
        self._registry = registry
        self._executor = ToolExecutor(registry)
        self._dispatcher = CommandDispatcher()

    async def respond(
        self,
        interaction_id: str,
        decision: InteractionDecision,
    ) -> StartupResult | None:
        pending = self._interactions.pending
        if pending is None or pending.id != interaction_id:
            return None
        if pending.kind is not InteractionKind.WORKSPACE_TRUST:
            raise ValueError("Execute interactions are resolved by their operation.")
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
        return await self._executor.execute(
            request,
            context=ToolExecutionContext(
                workspace=self._workspace,
                operation_id=new_identifier("operation"),
                turn_id=turn_id,
                emitter=self._emitter,
            ),
        )

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

    async def cancel(self, operation_id: str) -> bool:
        return await self._operations.cancel(operation_id)

    async def close(self) -> None:
        pending = self._interactions.pending
        if pending is not None:
            self._interactions.cancel_pending()
            await self._interactions.wait(pending.id)
        await self._operations.shutdown()
        self._journal = None
        self._change_operations = None
        self._registry = None
        self._executor = None
        self._dispatcher = None
