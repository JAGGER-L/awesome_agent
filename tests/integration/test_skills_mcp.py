import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio

from awesome_agent.agent import new_agent_state
from awesome_agent.application.command_results import (
    CommandInteractionResult,
    CommandResult,
    McpCommandPayload,
    SkillCatalogCommandPayload,
)
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.context import ApplicationContextService
from awesome_agent.application.extension_commands import ApplicationExtensionService
from awesome_agent.config import (
    BudgetConfig,
    TurnConfig,
    missing_provider_credential_statuses,
)
from awesome_agent.context import ContextBuilder
from awesome_agent.conversation import ConversationService
from awesome_agent.core.events import CollectingEventSink, EventEmitter, EventType
from awesome_agent.core.tools import (
    PermissionMode,
    PermissionSession,
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolRequest,
    ToolStatus,
)
from awesome_agent.core.tools.builtins import register_read_tools
from awesome_agent.core.tools.executor import ToolExecutor
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.extensions.mcp import (
    McpConnectionState,
    McpManager,
    McpServerConfig,
    McpSource,
)
from awesome_agent.extensions.skills import SkillLoader, discover_skills
from awesome_agent.storage import ApplicationSQLite, SQLiteMcpEnablementStore
from awesome_agent.storage.conversations import SQLiteConversationRepositories


def _skill(root: Path, name: str) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Workspace review\n---\n"
        "Review the workspace-specific invariants.",
        encoding="utf-8",
    )


@pytest_asyncio.fixture
async def application_database(tmp_path: Path) -> AsyncIterator[ApplicationSQLite]:
    database = ApplicationSQLite(tmp_path / "application.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_trusted_skill_and_mcp_vertical_lifecycle(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    skill_root = workspace_path / ".agents" / "skills"
    _skill(skill_root, "workspace-review")
    workspace = resolve_workspace(workspace_path)
    fixture = Path(__file__).parents[1] / "fixtures" / "mcp_stdio_server.py"
    fixture_config = McpServerConfig(
        id="fixture",
        command=sys.executable,
        args=("-u", str(fixture)),
        source=McpSource.WORKSPACE,
    )
    broken_config = McpServerConfig(
        id="broken",
        command=str(tmp_path / "missing-server"),
        source=McpSource.USER,
        enabled=True,
    )

    untrusted_catalog = discover_skills(
        bundled_root=None,
        user_root=None,
        workspace_root=skill_root,
        workspace_trusted=False,
    )
    assert untrusted_catalog.descriptors() == ()
    assert (
        McpManager(
            configs=(),
            workspace_trusted=False,
            enablements={},
            registry=ToolRegistry(),
        ).configs()
        == ()
    )

    catalog = discover_skills(
        bundled_root=None,
        user_root=None,
        workspace_root=skill_root,
        workspace_trusted=True,
    )
    loader = SkillLoader(catalog)
    repositories = SQLiteConversationRepositories(application_database)
    conversation = ConversationService(store=repositories)
    thread = await conversation.create_thread(workspace.key)
    enablements = SQLiteMcpEnablementStore(application_database)
    registry = ToolRegistry()
    register_read_tools(registry)
    manager = McpManager(
        configs=(fixture_config, broken_config),
        workspace_trusted=True,
        enablements=await enablements.snapshot(workspace.key),
        registry=registry,
    )

    extensions = ApplicationExtensionService(
        conversation=conversation,
        catalog=catalog,
        manager=manager,
        enablements=enablements,
        workspace_key=workspace.key,
        registry=registry,
        current_thread_id=lambda: thread.id,
        credential_statuses=missing_provider_credential_statuses,
    )
    selected = await extensions.skills(
        CommandIntent(name=CommandName.SKILLS, arguments=("workspace-review",)),
    )
    assert isinstance(selected, CommandResult)
    assert isinstance(selected.payload, SkillCatalogCommandPayload)
    assert selected.payload.active_mode == "workspace-review"
    assert (
        await conversation.read_thread(thread.id)
    ).thread.skill_mode == "workspace-review"

    configured_thread = (await conversation.read_thread(thread.id)).thread
    turn = await conversation.begin_turn(
        thread.id,
        "review this change",
        TurnConfig(
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
            skill_mode=configured_thread.skill_mode,
            budgets=BudgetConfig(),
        ),
        client_message_id="client_skills",
    )
    context_service = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=cast(Any, object()),
        configured_total_tokens=262_144,
        model_context_limit=262_144,
        product_instructions="product",
        skill_loader=loader,
    )
    context_service.prepare_turn(turn, "review this change")
    prepared = await context_service.build(
        new_agent_state(
            thread_id=thread.id,
            turn_id=turn.id,
            workspace_key=workspace.key,
            provider=turn.provider,
            model=turn.model,
            thinking_enabled=False,
        )
    )
    assert [item["source_id"] for item in prepared.manifest].count(
        "workspace-review"
    ) == 1

    enabled = await extensions.mcp(
        CommandIntent(name=CommandName.MCP, arguments=("enable", "fixture")),
    )
    assert isinstance(enabled, CommandResult)
    assert isinstance(enabled.payload, McpCommandPayload)
    assert enabled.payload.servers[0].state == McpConnectionState.CONFIGURED.value
    assert registry.resolve("mcp.fixture.echo") is None

    await extensions.prepare_turn_extensions()
    assert registry.resolve("mcp.fixture.echo") is not None
    assert registry.resolve("read_file") is not None
    assert manager.status("broken").state is McpConnectionState.ERROR

    sink = CollectingEventSink()

    async def approve(_: ToolApprovalRequest) -> ToolApprovalDecision:
        return ToolApprovalDecision.ALLOW_ONCE

    result = await ToolExecutor(registry).execute(
        ToolRequest(
            call_id="call_echo",
            tool_name="mcp.fixture.echo",
            arguments={"text": "hello"},
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
                sink=sink,
            ),
            activity_writer=repositories,
            monotonic=time.monotonic,
            permission_session=PermissionSession(mode=PermissionMode.FULL_ACCESS),
            approval_resolver=approve,
        ),
    )
    assert result.status is ToolStatus.SUCCESS
    assert result.content == "hello"
    assert [event.event_type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
    ]
    assert (await conversation.read_thread(thread.id)).tool_activities[0].tool_name == (
        "mcp.fixture.echo"
    )
    await manager.aclose()

    restarted_registry = ToolRegistry()
    register_read_tools(restarted_registry)
    restarted = McpManager(
        configs=(fixture_config,),
        workspace_trusted=True,
        enablements=await enablements.snapshot(workspace.key),
        registry=restarted_registry,
    )
    restarted_extensions = ApplicationExtensionService(
        conversation=conversation,
        catalog=catalog,
        manager=restarted,
        enablements=enablements,
        workspace_key=workspace.key,
        registry=restarted_registry,
        current_thread_id=lambda: thread.id,
        credential_statuses=missing_provider_credential_statuses,
    )
    assert restarted.status("fixture").state is McpConnectionState.CONFIGURED
    assert restarted_registry.resolve("mcp.fixture.echo") is None
    await restarted_extensions.prepare_turn_extensions()
    assert restarted_registry.resolve("mcp.fixture.echo") is not None
    status_result = await restarted_extensions.mcp(
        CommandIntent(name=CommandName.MCP, arguments=("status", "fixture")),
    )
    assert isinstance(status_result, CommandResult)
    assert isinstance(status_result.payload, McpCommandPayload)
    assert status_result.payload.servers[0].state == "connected"
    disabled = await restarted_extensions.mcp(
        CommandIntent(name=CommandName.MCP, arguments=("disable", "fixture")),
    )
    assert isinstance(disabled, CommandResult)
    assert isinstance(disabled.payload, McpCommandPayload)
    assert disabled.payload.servers[0].state == "enablement_required"
    assert restarted_registry.resolve("mcp.fixture.echo") is None
    await restarted_extensions.mcp(
        CommandIntent(name=CommandName.MCP, arguments=("enable", "fixture")),
    )
    await restarted.aclose()

    changed = fixture_config.model_copy(update={"args": (*fixture_config.args, "x")})
    invalidated_registry = ToolRegistry()
    register_read_tools(invalidated_registry)
    invalidated = McpManager(
        configs=(changed,),
        workspace_trusted=True,
        enablements=await enablements.snapshot(workspace.key),
        registry=invalidated_registry,
    )
    invalidated_extensions = ApplicationExtensionService(
        conversation=conversation,
        catalog=catalog,
        manager=invalidated,
        enablements=enablements,
        workspace_key=workspace.key,
        registry=invalidated_registry,
        current_thread_id=lambda: thread.id,
        credential_statuses=missing_provider_credential_statuses,
    )
    await invalidated_extensions.prepare_turn_extensions()
    assert invalidated_registry.resolve("mcp.fixture.echo") is None
    assert invalidated_registry.resolve("read_file") is not None


@pytest.mark.asyncio
async def test_skills_select_mode_without_submitting_a_hidden_turn(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    skill_root = tmp_path / "skills"
    for name in ("review", "debug", "test", "git-workflow"):
        _skill(skill_root, name)
    catalog = discover_skills(
        bundled_root=skill_root,
        user_root=None,
        workspace_root=None,
        workspace_trusted=False,
    )
    conversation = ConversationService(
        store=SQLiteConversationRepositories(application_database)
    )
    thread = await conversation.create_thread("workspace")
    enablements = SQLiteMcpEnablementStore(application_database)
    registry = ToolRegistry()
    service = ApplicationExtensionService(
        conversation=conversation,
        catalog=catalog,
        manager=McpManager(
            configs=(),
            workspace_trusted=True,
            enablements={},
            registry=registry,
        ),
        enablements=enablements,
        workspace_key="workspace",
        registry=registry,
        current_thread_id=lambda: thread.id,
        credential_statuses=missing_provider_credential_statuses,
    )

    listed = await service.skills(
        CommandIntent(name=CommandName.SKILLS),
    )
    picker = await service.skills(
        CommandIntent(name=CommandName.SKILLS),
    )
    assert isinstance(listed, CommandInteractionResult)
    assert isinstance(listed.context, SkillCatalogCommandPayload)
    assert len(listed.context.skills) == 4
    assert isinstance(picker, CommandInteractionResult)
    assert picker.interaction.kind == "selection"
    assert {option.value for option in picker.interaction.options} == {
        "auto",
        "off",
        "review",
        "debug",
        "test",
        "git-workflow",
    }
