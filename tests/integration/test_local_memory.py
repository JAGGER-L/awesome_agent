import asyncio
import threading
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel

from awesome_agent.agent import new_agent_state
from awesome_agent.application.command_results import (
    CommandError,
    CommandInteractionResult,
    CommandResult,
    MemoryDocumentCommandPayload,
    MemoryMutationCommandPayload,
    MemoryStatusCommandPayload,
)
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.context import ApplicationContextService
from awesome_agent.application.extension_commands import ApplicationExtensionService
from awesome_agent.config import (
    BudgetConfig,
    TurnConfig,
    UserConfigDocument,
    UserConfigWriter,
    missing_provider_credential_statuses,
)
from awesome_agent.config.loader import read_user_config_document
from awesome_agent.config.resource_lock import exclusive_resource_lock
from awesome_agent.context import ContextBuilder
from awesome_agent.conversation import ConversationService, UsageSummary
from awesome_agent.core.events import CollectingEventSink, EventEmitter
from awesome_agent.core.tools import (
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolOutput,
    ToolRequest,
    ToolSpec,
    ToolStatus,
)
from awesome_agent.core.tools.builtins import register_read_tools
from awesome_agent.core.tools.executor import ToolExecutor
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.extensions.mcp import McpManager
from awesome_agent.extensions.skills import SkillCatalog
from awesome_agent.memory import (
    LocalMemoryService,
    MemoryMutationStatus,
    MemoryScope,
)
from awesome_agent.paths import AwesomePaths
from awesome_agent.storage import ApplicationSQLite, SQLiteMcpEnablementStore
from awesome_agent.storage.conversations import SQLiteConversationRepositories


@pytest.fixture
async def application_database(tmp_path: Path) -> AsyncIterator[ApplicationSQLite]:
    paths = AwesomePaths.from_home(tmp_path / "home")
    database = ApplicationSQLite(paths.application_db)
    await database.initialize()
    try:
        yield database
    finally:
        await database.aclose()


def _config() -> TurnConfig:
    return TurnConfig(
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        budgets=BudgetConfig(),
    )


def _hold_resource_lock(path: Path, entered: threading.Event, seconds: float) -> None:
    with exclusive_resource_lock(path):
        entered.set()
        time.sleep(seconds)


def _fill_registry(registry: ToolRegistry, count: int) -> None:
    async def placeholder_handler(
        _arguments: BaseModel,
        _context: ToolExecutionContext,
    ) -> ToolOutput:
        return ToolOutput(content="unused")

    for index in range(count):
        registry.register(
            spec=ToolSpec(
                name=f"placeholder_{index}",
                description="Reserved test tool.",
                input_schema={},
                capability="workspace.read",
                read_only=True,
            ),
            input_model=BaseModel,
            handler=placeholder_handler,
        )


@pytest.mark.asyncio
async def test_local_memory_command_lock_wait_keeps_event_loop_schedulable(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    paths = AwesomePaths.from_home(tmp_path / "home")
    conversation = ConversationService(
        store=SQLiteConversationRepositories(application_database)
    )
    thread = await conversation.create_thread(workspace.key)
    registry = ToolRegistry()
    memory = LocalMemoryService(
        paths=paths,
        workspace_key=workspace.key,
        enabled=True,
        id_factory=lambda: "memory_11111111111111111111111111111111",
    )
    enablements = SQLiteMcpEnablementStore(application_database)
    extensions = ApplicationExtensionService(
        conversation=conversation,
        catalog=SkillCatalog((), ()),
        manager=McpManager(
            configs=(),
            workspace_trusted=True,
            enablements=await enablements.snapshot(workspace.key),
            registry=registry,
        ),
        enablements=enablements,
        workspace_key=workspace.key,
        registry=registry,
        current_thread_id=lambda: thread.id,
        credential_statuses=missing_provider_credential_statuses,
        local_memory=memory,
    )
    entered = threading.Event()
    holder = threading.Thread(
        target=_hold_resource_lock,
        args=(paths.user_memory_file, entered, 0.4),
        daemon=True,
    )
    holder.start()
    assert await asyncio.to_thread(entered.wait, 1.0)

    operation = asyncio.create_task(
        extensions.memory(
            CommandIntent(
                name=CommandName.MEMORY,
                arguments=("add", "user", "Remember", "this."),
            )
        )
    )
    heartbeat = asyncio.create_task(asyncio.sleep(0.05))
    try:
        await asyncio.wait_for(heartbeat, timeout=0.2)
        assert not operation.done()
        outcome = await operation
    finally:
        await asyncio.to_thread(holder.join, 1.0)

    assert isinstance(outcome, CommandResult)
    assert isinstance(outcome.payload, MemoryMutationCommandPayload)
    assert outcome.payload.status == MemoryMutationStatus.ADDED.value


@pytest.mark.asyncio
async def test_local_memory_enable_capacity_failure_changes_no_state(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    paths = AwesomePaths.from_home(tmp_path / "home")
    conversation = ConversationService(
        store=SQLiteConversationRepositories(application_database)
    )
    thread = await conversation.create_thread(workspace.key)
    registry = ToolRegistry()
    _fill_registry(registry, 125)
    before = registry.specifications()
    local_memory = LocalMemoryService(paths=paths, workspace_key=workspace.key)
    enablements = SQLiteMcpEnablementStore(application_database)
    extensions = ApplicationExtensionService(
        conversation=conversation,
        catalog=SkillCatalog((), ()),
        manager=McpManager(
            configs=(),
            workspace_trusted=True,
            enablements=await enablements.snapshot(workspace.key),
            registry=registry,
        ),
        enablements=enablements,
        workspace_key=workspace.key,
        registry=registry,
        current_thread_id=lambda: thread.id,
        credential_statuses=missing_provider_credential_statuses,
        local_memory=local_memory,
        config_writer=UserConfigWriter(paths.config_file),
    )

    outcome = await extensions.memory(
        CommandIntent(name=CommandName.MEMORY, arguments=("local", "on")),
    )

    assert isinstance(outcome, CommandError)
    assert outcome.code == "tool_registry_limit"
    assert local_memory.enabled is False
    assert (
        read_user_config_document(paths.config_file).memory.local_file_memory is False
    )
    assert registry.specifications() == before


@pytest.mark.asyncio
async def test_local_memory_enable_config_failure_changes_no_runtime_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    paths = AwesomePaths.from_home(tmp_path / "home")
    conversation = ConversationService(
        store=SQLiteConversationRepositories(application_database)
    )
    thread = await conversation.create_thread(workspace.key)
    registry = ToolRegistry()
    _fill_registry(registry, 1)
    before = registry.specifications()
    local_memory = LocalMemoryService(paths=paths, workspace_key=workspace.key)
    writer = UserConfigWriter(paths.config_file)
    enablements = SQLiteMcpEnablementStore(application_database)
    extensions = ApplicationExtensionService(
        conversation=conversation,
        catalog=SkillCatalog((), ()),
        manager=McpManager(
            configs=(),
            workspace_trusted=True,
            enablements=await enablements.snapshot(workspace.key),
            registry=registry,
        ),
        enablements=enablements,
        workspace_key=workspace.key,
        registry=registry,
        current_thread_id=lambda: thread.id,
        credential_statuses=missing_provider_credential_statuses,
        local_memory=local_memory,
        config_writer=writer,
    )

    def fail_update(
        _transform: Callable[[UserConfigDocument], UserConfigDocument],
    ) -> UserConfigDocument:
        raise OSError("simulated config persistence failure")

    monkeypatch.setattr(writer, "update", fail_update)
    with pytest.raises(OSError, match="simulated config persistence failure"):
        await extensions.memory(
            CommandIntent(name=CommandName.MEMORY, arguments=("local", "on")),
        )

    assert local_memory.enabled is False
    assert (
        read_user_config_document(paths.config_file).memory.local_file_memory is False
    )
    assert registry.specifications() == before


@pytest.mark.asyncio
async def test_cancelled_local_memory_disable_commits_one_coherent_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    paths = AwesomePaths.from_home(tmp_path / "home")
    conversation = ConversationService(
        store=SQLiteConversationRepositories(application_database)
    )
    thread = await conversation.create_thread(workspace.key)
    registry = ToolRegistry()
    _fill_registry(registry, 1)
    local_memory = LocalMemoryService(paths=paths, workspace_key=workspace.key)
    writer = UserConfigWriter(paths.config_file)
    enablements = SQLiteMcpEnablementStore(application_database)
    extensions = ApplicationExtensionService(
        conversation=conversation,
        catalog=SkillCatalog((), ()),
        manager=McpManager(
            configs=(),
            workspace_trusted=True,
            enablements=await enablements.snapshot(workspace.key),
            registry=registry,
        ),
        enablements=enablements,
        workspace_key=workspace.key,
        registry=registry,
        current_thread_id=lambda: thread.id,
        credential_statuses=missing_provider_credential_statuses,
        local_memory=local_memory,
        config_writer=writer,
    )
    enabled = await extensions.memory(
        CommandIntent(name=CommandName.MEMORY, arguments=("local", "on")),
    )
    assert isinstance(enabled, CommandResult)
    entered = threading.Event()
    release = threading.Event()
    original_update = writer.update

    def delayed_update(
        transform: Callable[[UserConfigDocument], UserConfigDocument],
    ) -> UserConfigDocument:
        entered.set()
        if not release.wait(1.0):
            raise RuntimeError("local memory disable release was not scheduled")
        return original_update(transform)

    monkeypatch.setattr(writer, "update", delayed_update)
    task = asyncio.create_task(
        extensions.memory(
            CommandIntent(name=CommandName.MEMORY, arguments=("local", "off")),
        )
    )
    assert await asyncio.to_thread(entered.wait, 1.0)

    task.cancel("cancel local memory disable")
    try:
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError, match="cancel local memory disable"):
        await asyncio.wait_for(task, timeout=1.0)

    assert local_memory.enabled is False
    assert (
        read_user_config_document(paths.config_file).memory.local_file_memory is False
    )
    assert {spec.name for spec in registry.specifications()} == {"placeholder_0"}


@pytest.mark.asyncio
async def test_offline_command_tool_context_conflict_and_restart_flow(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    paths = AwesomePaths.from_home(tmp_path / "home")
    repositories = SQLiteConversationRepositories(application_database)
    conversation = ConversationService(store=repositories)
    thread = await conversation.create_thread(workspace.key)
    ids = iter(f"memory_{index:032x}" for index in range(1, 10))
    memory = LocalMemoryService(
        paths=paths,
        workspace_key=workspace.key,
        id_factory=lambda: next(ids),
    )
    registry = ToolRegistry()
    register_read_tools(registry)
    catalog = SkillCatalog((), ())
    enablements = SQLiteMcpEnablementStore(application_database)

    extensions = ApplicationExtensionService(
        conversation=conversation,
        catalog=catalog,
        manager=McpManager(
            configs=(),
            workspace_trusted=True,
            enablements=await enablements.snapshot(workspace.key),
            registry=registry,
        ),
        enablements=enablements,
        workspace_key=workspace.key,
        registry=registry,
        current_thread_id=lambda: thread.id,
        credential_statuses=missing_provider_credential_statuses,
        local_memory=memory,
        config_writer=UserConfigWriter(paths.config_file),
    )

    initial = await extensions.memory(
        CommandIntent(name=CommandName.MEMORY),
    )
    assert isinstance(initial, CommandInteractionResult)
    assert initial.interaction.kind == "selection"
    assert [option.value for option in initial.interaction.options] == [
        "local",
        "mem0",
    ]
    assert isinstance(initial.context, MemoryStatusCommandPayload)
    assert initial.context.local_enabled is False
    local = await extensions.memory(
        CommandIntent(name=CommandName.MEMORY, arguments=("local",)),
    )
    assert isinstance(local, CommandInteractionResult)
    assert local.interaction.kind == "selection"
    assert [option.value for option in local.interaction.options] == ["off", "on"]
    assert local.interaction.options[0].selected is True

    enabled = await extensions.memory(
        CommandIntent(name=CommandName.MEMORY, arguments=("local", "on")),
    )
    assert isinstance(enabled, CommandResult)
    assert read_user_config_document(paths.config_file).memory.local_file_memory is True
    assert registry.resolve("memory_add") is not None

    for scope, content in (
        ("user", ("Prefer", "concise", "answers.")),
        ("workspace", ("Project", "uses", "pytest.")),
    ):
        result = await extensions.memory(
            CommandIntent(
                name=CommandName.MEMORY,
                arguments=("add", scope, *content),
            ),
        )
        assert isinstance(result, CommandResult)

    listed = await extensions.memory(
        CommandIntent(name=CommandName.MEMORY, arguments=("list", "user")),
    )
    assert isinstance(listed, CommandResult)
    assert isinstance(listed.payload, MemoryDocumentCommandPayload)
    entries = listed.payload.entries
    assert entries[0].content == "Prefer concise answers."

    turn = await conversation.begin_turn(
        thread.id,
        "remember editor preference",
        _config(),
        client_message_id="client_memory",
    )
    observed = memory.snapshot(MemoryScope.USER)
    tool_result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="memory_tool",
            tool_name="memory_add",
            arguments={
                "scope": "user",
                "content": "Preferred editor is Neovim.",
                "expected_hash": observed.content_hash,
            },
        ),
        context=ToolExecutionContext(
            workspace=workspace,
            thread_id=thread.id,
            operation_id="operation",
            turn_id=turn.id,
            origin=ToolExecutionOrigin.AGENT,
            emitter=EventEmitter(
                session_id="session",
                workspace_key=workspace.key,
                sink=CollectingEventSink(),
            ),
            activity_writer=repositories,
            monotonic=time.monotonic,
        ),
    )
    assert tool_result.status is ToolStatus.SUCCESS
    assert (await conversation.read_thread(thread.id)).tool_activities[
        -1
    ].tool_name == "memory_add"

    context_service = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=cast(Any, object()),
        configured_total_tokens=100_000,
        model_context_limit=100_000,
        product_instructions="product",
        local_memory=memory,
    )
    context_service.prepare_turn(turn, "remember editor preference")

    user_path = paths.user_memory_file
    user_path.write_bytes(user_path.read_bytes() + b"\nManual free Markdown.\n")
    command_add = await extensions.memory(
        CommandIntent(
            name=CommandName.MEMORY,
            arguments=("add", "user", "Use", "UTC", "timestamps."),
        ),
    )
    assert isinstance(command_add, CommandResult)
    assert b"Manual free Markdown." in user_path.read_bytes()

    stale = memory.snapshot(MemoryScope.WORKSPACE)
    workspace_file = paths.workspace_memory_file(workspace.key)
    workspace_file.write_bytes(workspace_file.read_bytes() + b"\nmanual change\n")
    conflict = memory.add(
        MemoryScope.WORKSPACE,
        "Another fact.",
        expected_hash=stale.content_hash,
    )
    assert conflict.status is MemoryMutationStatus.CONFLICT

    disabled = await extensions.memory(
        CommandIntent(name=CommandName.MEMORY, arguments=("local", "off")),
    )
    assert isinstance(disabled, CommandResult)
    assert registry.resolve("memory_add") is None
    assert user_path.is_file() and workspace_file.is_file()

    frozen = await context_service.build(
        new_agent_state(
            thread_id=thread.id,
            turn_id=turn.id,
            workspace_key=workspace.key,
            provider=turn.provider,
            model=turn.model,
            thinking_enabled=False,
        )
    )
    assert any(item["kind"] == "user_memory" for item in frozen.manifest)
    await conversation.complete_turn(turn.id, "done", UsageSummary(), "completed")

    next_turn = await conversation.begin_turn(
        thread.id, "next", _config(), client_message_id="client_next"
    )
    context_service.prepare_turn(next_turn, "next")
    next_context = await context_service.build(
        new_agent_state(
            thread_id=thread.id,
            turn_id=next_turn.id,
            workspace_key=workspace.key,
            provider=next_turn.provider,
            model=next_turn.model,
            thinking_enabled=False,
        )
    )
    assert not any(
        item["kind"] in {"user_memory", "workspace_memory"}
        for item in next_context.manifest
    )

    restarted_config = read_user_config_document(paths.config_file)
    restarted = LocalMemoryService(
        paths=paths,
        workspace_key=workspace.key,
        enabled=restarted_config.memory.local_file_memory,
    )
    assert restarted.enabled is False
    assert len(restarted.list(MemoryScope.USER)) == 3
    assert "Manual free Markdown." in restarted.snapshot(MemoryScope.USER).markdown


@pytest.mark.asyncio
async def test_memory_command_grammar_and_mem0_are_explicit(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    paths = AwesomePaths.from_home(tmp_path / "home")
    repositories = SQLiteConversationRepositories(application_database)
    conversation = ConversationService(store=repositories)
    thread = await conversation.create_thread(workspace.key)
    catalog = SkillCatalog((), ())
    registry = ToolRegistry()
    enablements = SQLiteMcpEnablementStore(application_database)

    service = ApplicationExtensionService(
        conversation=conversation,
        catalog=catalog,
        manager=McpManager(
            configs=(),
            workspace_trusted=True,
            enablements=await enablements.snapshot(workspace.key),
            registry=registry,
        ),
        enablements=enablements,
        workspace_key=workspace.key,
        registry=registry,
        current_thread_id=lambda: thread.id,
        credential_statuses=missing_provider_credential_statuses,
        local_memory=LocalMemoryService(paths=paths, workspace_key=workspace.key),
        config_writer=UserConfigWriter(paths.config_file),
    )

    mem0 = await service.memory(
        CommandIntent(name=CommandName.MEMORY, arguments=("mem0", "on")),
    )
    invalid = await service.memory(
        CommandIntent(name=CommandName.MEMORY, arguments=("unknown",)),
    )
    await service.memory(
        CommandIntent(name=CommandName.MEMORY, arguments=("local", "on")),
    )
    added = await service.memory(
        CommandIntent(
            name=CommandName.MEMORY,
            arguments=("add", "user", "Original", "preference."),
        ),
    )
    assert isinstance(added, CommandResult)
    assert isinstance(added.payload, MemoryMutationCommandPayload)
    assert added.payload.entry_id is not None
    entry_id = added.payload.entry_id
    replaced = await service.memory(
        CommandIntent(
            name=CommandName.MEMORY,
            arguments=("replace", "user", entry_id, "Updated", "preference."),
        ),
    )
    removed = await service.memory(
        CommandIntent(
            name=CommandName.MEMORY,
            arguments=("remove", "user", entry_id),
        ),
    )

    assert isinstance(mem0, CommandError) and mem0.code == "mem0_credential_unavailable"
    assert "/auth" in mem0.message
    assert read_user_config_document(paths.config_file).memory.mem0_cloud is False
    assert isinstance(invalid, CommandError) and invalid.code == "invalid_arguments"
    assert isinstance(replaced, CommandResult)
    assert isinstance(replaced.payload, MemoryMutationCommandPayload)
    assert replaced.payload.status == "replaced"
    assert isinstance(removed, CommandResult)
    assert isinstance(removed.payload, MemoryMutationCommandPayload)
    assert removed.payload.status == "removed"
