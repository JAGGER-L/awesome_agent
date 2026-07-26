from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from awesome_agent.application.command_results import (
    CommandOption,
    CommandOutcome,
    CommandSelection,
    McpCommandItem,
    McpCommandPayload,
    MemoryCommandEntry,
    MemoryDocumentCommandPayload,
    MemoryMutationCommandPayload,
    MemorySearchCommandPayload,
    MemorySearchItem,
    MemoryStatusCommandPayload,
    SkillCatalogCommandPayload,
    SkillCommandDiagnostic,
    SkillCommandItem,
    error,
    interaction,
    result,
)
from awesome_agent.application.commands import CommandIntent
from awesome_agent.config import (
    ProviderCredentialStatuses,
    UserConfigDocument,
    UserConfigWriter,
)
from awesome_agent.conversation import ConversationService
from awesome_agent.core.cancellation import (
    finish_cancellation_safe,
    run_cancellation_safe_blocking_call,
)
from awesome_agent.core.tools.registry import ToolRegistry, ToolRegistryLimitError
from awesome_agent.extensions.mcp import (
    McpConnectionState,
    McpManager,
    McpServerStatus,
    McpSource,
    McpUnavailable,
)
from awesome_agent.extensions.mcp.models import mcp_config_hash
from awesome_agent.extensions.skills import SkillCatalog, SkillNotFound
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
    new_mem0_user_id,
    refresh_local_memory_tools,
    validate_local_memory_tools,
)
from awesome_agent.storage.mcp import SQLiteMcpEnablementStore

type Mem0StateChanged = Callable[[bool, Mem0Identity | None], None]


class ApplicationExtensionService:
    """Own Skill, MCP, and Memory command semantics."""

    def __init__(
        self,
        *,
        conversation: ConversationService,
        catalog: SkillCatalog,
        manager: McpManager,
        enablements: SQLiteMcpEnablementStore,
        workspace_key: str,
        registry: ToolRegistry,
        current_thread_id: Callable[[], str | None],
        credential_statuses: Callable[[], ProviderCredentialStatuses],
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
        self._manager = manager
        self._enablements = enablements
        self._workspace_key = workspace_key
        self._registry = registry
        self._current_thread_id = current_thread_id
        self._credential_statuses = credential_statuses
        self._local_memory = local_memory
        self._config_writer = config_writer
        self._mem0_cloud = mem0_cloud
        self._mem0_enabled = mem0_enabled
        self._mem0_user_id = mem0_user_id
        self._mem0_initialization_diagnostic = mem0_initialization_diagnostic
        self._mem0_identity: Mem0Identity | None = None
        self._mem0_state_changed = mem0_state_changed
        self._has_active_turn = has_active_turn

    async def prepare_turn_extensions(self) -> None:
        await self._manager.start_enabled()

    async def skills(self, intent: CommandIntent) -> CommandOutcome:
        thread_id = self._current_thread_id()
        if thread_id is None:
            return error("thread_not_found", "Select a Thread first.")
        if intent.arguments:
            if len(intent.arguments) != 1:
                return error("invalid_arguments", "Usage: /skills [auto|off|name]")
            selected = intent.arguments[0]
            if selected not in {"auto", "off"}:
                try:
                    self._catalog.resolve(selected)
                except SkillNotFound:
                    return error("skill_not_found", "Skill was not found.")
            updated = await self._conversation.set_skill_mode(thread_id, selected)
            return result(self._skill_payload(updated.skill_mode))
        current = (await self._conversation.read_thread(thread_id)).thread.skill_mode
        payload = self._skill_payload(current)
        return interaction(
            CommandSelection(
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
            context=payload,
        )

    async def mcp(self, intent: CommandIntent) -> CommandOutcome:
        arguments = intent.arguments
        if not arguments:
            return self._mcp_status(None)
        action = arguments[0]
        if action == "status" and len(arguments) in {1, 2}:
            return self._mcp_status(arguments[1] if len(arguments) == 2 else None)
        if action not in {"enable", "disable", "restart"} or len(arguments) != 2:
            return error(
                "invalid_arguments",
                "Usage: /mcp [status [id]|enable <id>|disable <id>|restart <id>]",
            )
        server_id = arguments[1]
        try:
            config = self._manager.config(server_id)
        except McpUnavailable:
            return error("mcp_server_not_found", "MCP server was not found.")
        if action in {"enable", "disable"} and config.source is McpSource.USER:
            return error(
                "user_config_required",
                "User MCP enablement is controlled by user configuration.",
            )
        if action == "enable":
            status = await _finish_mcp_enablement(
                self._enablements.enable(
                    self._workspace_key, config.id, mcp_config_hash(config)
                ),
                manager=self._manager,
                server_id=config.id,
                config_hash=mcp_config_hash(config),
            )
        elif action == "disable":
            status = await _finish_mcp_enablement(
                self._enablements.disable(self._workspace_key, config.id),
                manager=self._manager,
                server_id=config.id,
                config_hash=None,
            )
        else:
            status = await self._manager.restart(config.id)
        if status.state is McpConnectionState.ERROR:
            return error("mcp_server_error", status.detail or "MCP server failed.")
        return result(McpCommandPayload(servers=(self._mcp_item(status),)))

    async def memory(self, intent: CommandIntent) -> CommandOutcome:
        arguments = intent.arguments
        if not arguments:
            return interaction(
                CommandSelection(
                    prompt="Choose a memory system.",
                    options=(
                        CommandOption(value="local", label="Local memory"),
                        CommandOption(value="mem0", label="Cloud memory · Mem0"),
                    ),
                ),
                context=self._memory_status(),
            )
        if len(arguments) == 1 and arguments[0] in {"local", "mem0"}:
            enabled = (
                self._local_memory.status().enabled
                if arguments[0] == "local" and self._local_memory is not None
                else self._mem0_enabled
                if arguments[0] == "mem0"
                else False
            )
            return interaction(
                CommandSelection(
                    prompt=(
                        "Local memory"
                        if arguments[0] == "local"
                        else "Cloud memory · Mem0"
                    ),
                    options=(
                        CommandOption(value="off", label="Off", selected=not enabled),
                        CommandOption(value="on", label="On", selected=enabled),
                    ),
                ),
                context=self._memory_status(),
            )
        if arguments[0] == "mem0":
            return await self._mem0_command(arguments[1:])
        service = self._local_memory
        if service is None:
            return error("command_not_available", "Local memory is not available.")
        action = arguments[0]
        if action == "local" and len(arguments) == 2 and arguments[1] in {"on", "off"}:
            writer = self._config_writer
            if writer is None:
                return error(
                    "command_not_available", "User configuration is not writable."
                )
            if self._has_active_turn():
                return error(
                    "turn_busy", "Change local memory after the active Turn completes."
                )
            enabled = arguments[1] == "on"

            try:
                validate_local_memory_tools(
                    self._registry,
                    service,
                    enabled=enabled,
                )
            except ToolRegistryLimitError:
                return error(
                    "tool_registry_limit",
                    "Local memory tools do not fit safely in the current tool catalog.",
                )

            def update(document: UserConfigDocument) -> UserConfigDocument:
                memory = document.memory.model_copy(
                    update={"local_file_memory": enabled}
                )
                return document.model_copy(update={"memory": memory})

            def commit_local_state(_: object) -> None:
                refresh_local_memory_tools(
                    self._registry,
                    service,
                    enabled=enabled,
                )
                service.set_enabled(enabled)

            await run_cancellation_safe_blocking_call(
                lambda: writer.update(update),
                on_completed=commit_local_state,
            )
            return result(self._memory_status())
        if action == "list" and len(arguments) == 2:
            scope = _memory_scope(arguments[1])
            if scope is None:
                return self._memory_usage_error()
            selected_scope = scope
            document = await run_cancellation_safe_blocking_call(
                lambda: service.snapshot(selected_scope)
            )
            return result(
                MemoryDocumentCommandPayload(
                    scope=scope.value,
                    content_hash=document.content_hash,
                    entries=tuple(
                        MemoryCommandEntry(id=item.id, content=item.content)
                        for item in document.entries
                    ),
                )
            )
        if action == "add" and len(arguments) >= 3:
            scope = _memory_scope(arguments[1])
            if scope is None:
                return self._memory_usage_error()
            selected_scope = scope
            mutation = await run_cancellation_safe_blocking_call(
                lambda: _add_observed_memory(
                    service,
                    selected_scope,
                    " ".join(arguments[2:]),
                )
            )
            return _memory_mutation_result(mutation)
        if action == "replace" and len(arguments) >= 4:
            scope = _memory_scope(arguments[1])
            if scope is None:
                return self._memory_usage_error()
            selected_scope = scope
            mutation = await run_cancellation_safe_blocking_call(
                lambda: _replace_observed_memory(
                    service,
                    selected_scope,
                    arguments[2],
                    " ".join(arguments[3:]),
                )
            )
            return _memory_mutation_result(mutation)
        if action == "remove" and len(arguments) == 3:
            scope = _memory_scope(arguments[1])
            if scope is None:
                return self._memory_usage_error()
            mutation = await run_cancellation_safe_blocking_call(
                lambda: _remove_observed_memory(service, scope, arguments[2])
            )
            return _memory_mutation_result(mutation)
        return self._memory_usage_error()

    async def _mem0_command(self, arguments: tuple[str, ...]) -> CommandOutcome:
        if arguments in {("on",), ("off",)}:
            writer = self._config_writer
            if writer is None:
                return error(
                    "command_not_available", "User configuration is not writable."
                )
            if self._has_active_turn():
                return error(
                    "turn_busy", "Change Mem0 after the active Turn completes."
                )
            enabled = arguments[0] == "on"
            if enabled and not self._credential_statuses().mem0.configured:
                return error(
                    "mem0_credential_unavailable",
                    "Select or configure a Mem0 credential with /auth before "
                    "enabling Cloud memory.",
                )
            if enabled and self._mem0_cloud is None:
                diagnostic = self._mem0_initialization_diagnostic or Mem0Diagnostic(
                    code="mem0_unavailable", operation="initialize"
                )
                return error(diagnostic.code, "Mem0 Cloud is not available.")

            def update(document: UserConfigDocument) -> UserConfigDocument:
                user_id = document.memory.mem0_user_id
                if enabled and user_id is None:
                    user_id = new_mem0_user_id()
                memory = document.memory.model_copy(
                    update={"mem0_cloud": enabled, "mem0_user_id": user_id}
                )
                return document.model_copy(update={"memory": memory})

            def commit_mem0_state(updated: UserConfigDocument) -> None:
                if enabled:
                    user_id = updated.memory.mem0_user_id
                    if user_id is None:
                        raise RuntimeError("Mem0 identity persistence failed")
                    self._mem0_identity = Mem0Identity(
                        user_id=user_id,
                        workspace_key=self._workspace_key,
                    )
                    self._mem0_user_id = user_id
                self._mem0_enabled = enabled
                self._mem0_state_changed(enabled, self._mem0_identity)

            await run_cancellation_safe_blocking_call(
                lambda: writer.update(update),
                on_completed=commit_mem0_state,
            )
            return result(self._memory_status())
        if not self._mem0_enabled:
            return error("memory_disabled", "Mem0 Cloud memory is disabled.")
        adapter = self._mem0_cloud
        if adapter is None:
            diagnostic = self._mem0_initialization_diagnostic or Mem0Diagnostic(
                code="mem0_unavailable", operation="initialize"
            )
            return error(diagnostic.code, "Mem0 Cloud is not available.")
        identity = self._current_mem0_identity()
        if identity is None:
            return error("mem0_identity_missing", "Mem0 Cloud identity is unavailable.")
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
            except Mem0CloudError as cloud_error:
                return error(
                    cloud_error.diagnostic.code, "Mem0 Cloud search did not complete."
                )
            return result(
                MemorySearchCommandPayload(
                    memories=tuple(
                        MemorySearchItem(
                            id=item.id,
                            content=item.content,
                            scope=item.scope.value,
                            fact_hash=item.fact_hash,
                        )
                        for item in memories
                    )
                )
            )
        if len(arguments) == 2 and arguments[0] == "remove":
            deleted = await adapter.remove_scoped(arguments[1], identity)
            if deleted.status is not CloudDeleteStatus.REMOVED:
                return error(
                    deleted.diagnostic.code
                    if deleted.diagnostic
                    else deleted.status.value,
                    "Cloud memory was not removed.",
                )
            return result(
                MemoryMutationCommandPayload(
                    provider="mem0",
                    status=deleted.status.value,
                    memory_id=deleted.memory_id,
                )
            )
        return self._memory_usage_error()

    def _skill_payload(self, active_mode: str) -> SkillCatalogCommandPayload:
        return SkillCatalogCommandPayload(
            active_mode=active_mode,
            skills=tuple(
                SkillCommandItem(
                    name=item.name,
                    description=item.description,
                    source=item.source.value,
                )
                for item in self._catalog.descriptors()
            ),
            diagnostics=tuple(
                SkillCommandDiagnostic(
                    code=item.code,
                    name=item.name,
                    source=item.source.value,
                    message=item.message,
                )
                for item in self._catalog.diagnostics()
            ),
        )

    def _mcp_status(self, server_id: str | None) -> CommandOutcome:
        statuses: tuple[McpServerStatus, ...]
        if server_id is not None:
            try:
                statuses = (self._manager.status(server_id),)
            except McpUnavailable:
                return error("mcp_server_not_found", "MCP server was not found.")
        else:
            statuses = self._manager.statuses()
        return result(
            McpCommandPayload(servers=tuple(self._mcp_item(item) for item in statuses))
        )

    @staticmethod
    def _mcp_item(status: McpServerStatus) -> McpCommandItem:
        return McpCommandItem(
            server_id=status.server_id, state=status.state.value, detail=status.detail
        )

    def _memory_status(self) -> MemoryStatusCommandPayload:
        local = self._local_memory.status() if self._local_memory is not None else None
        return MemoryStatusCommandPayload(
            local_available=local is not None,
            local_enabled=local.enabled if local is not None else False,
            cloud_available=self._mem0_cloud is not None,
            cloud_enabled=self._mem0_enabled,
            cloud_error_code=(
                self._mem0_initialization_diagnostic.code
                if self._mem0_initialization_diagnostic is not None
                else None
            ),
        )

    def _current_mem0_identity(self) -> Mem0Identity | None:
        if self._mem0_identity is not None:
            return self._mem0_identity
        if self._mem0_user_id is None:
            return None
        self._mem0_identity = Mem0Identity(
            user_id=self._mem0_user_id, workspace_key=self._workspace_key
        )
        return self._mem0_identity

    @staticmethod
    def _memory_usage_error() -> CommandOutcome:
        return error(
            "invalid_arguments",
            "Usage: /memory [local on|off|list <scope>|add <scope> <content>|"
            "replace <scope> <id> <content>|remove <scope> <id>|mem0 on|off|"
            "search <query>|remove <id>]",
        )


def _memory_scope(value: str) -> MemoryScope | None:
    try:
        return MemoryScope(value)
    except ValueError:
        return None


def _add_observed_memory(
    service: LocalMemoryService,
    scope: MemoryScope,
    content: str,
) -> MemoryMutationResult:
    observed = service.snapshot(scope)
    return service.add(scope, content, expected_hash=observed.content_hash)


def _replace_observed_memory(
    service: LocalMemoryService,
    scope: MemoryScope,
    entry_id: str,
    content: str,
) -> MemoryMutationResult:
    observed = service.snapshot(scope)
    return service.replace(
        scope,
        entry_id,
        content,
        expected_hash=observed.content_hash,
    )


def _remove_observed_memory(
    service: LocalMemoryService,
    scope: MemoryScope,
    entry_id: str,
) -> MemoryMutationResult:
    observed = service.snapshot(scope)
    return service.remove(scope, entry_id, expected_hash=observed.content_hash)


def _memory_mutation_result(mutation: MemoryMutationResult) -> CommandOutcome:
    if mutation.status not in {
        MemoryMutationStatus.ADDED,
        MemoryMutationStatus.REPLACED,
        MemoryMutationStatus.REMOVED,
    }:
        return error(
            mutation.error_code or mutation.status.value,
            "Local memory was not updated.",
        )
    return result(
        MemoryMutationCommandPayload(
            provider="local",
            status=mutation.status.value,
            scope=mutation.scope.value,
            entry_id=mutation.entry_id,
            error_code=mutation.error_code,
        )
    )


async def _finish_mcp_enablement(
    persistence: Awaitable[None],
    *,
    manager: McpManager,
    server_id: str,
    config_hash: str | None,
) -> McpServerStatus:
    async def persist_and_publish() -> McpServerStatus:
        await persistence
        manager.publish_enablement(server_id, config_hash)
        return await manager.refresh_enablement(server_id)

    task = asyncio.create_task(
        persist_and_publish(),
        name=f"mcp-enablement:{server_id}",
    )
    result_status, cancellation = await finish_cancellation_safe(task)
    if cancellation is not None:
        raise cancellation
    return result_status
