import sys
import time
from pathlib import Path
from typing import Any, cast

import pytest

from awesome_agent.agent import new_agent_state
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.context import ApplicationContextService
from awesome_agent.application.headless import ApplicationExtensionService
from awesome_agent.config import BudgetConfig, TurnConfig
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
from awesome_agent.storage import SQLiteMcpEnablementStore
from awesome_agent.storage.conversations import SQLiteConversationRepositories


def _skill(root: Path, name: str) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Workspace review\n---\n"
        "Review the workspace-specific invariants.",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_trusted_skill_and_mcp_vertical_lifecycle(tmp_path: Path) -> None:
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
            workspace_key=workspace.key,
            workspace_trusted=False,
            enablements=SQLiteMcpEnablementStore(tmp_path / "untrusted.db"),
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
    database = tmp_path / "application.db"
    repositories = SQLiteConversationRepositories(database)
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread(workspace.key)
    enablements = SQLiteMcpEnablementStore(database)
    manager = McpManager(
        configs=(fixture_config, broken_config),
        workspace_key=workspace.key,
        workspace_trusted=True,
        enablements=enablements,
    )
    registry = ToolRegistry()
    register_read_tools(registry)

    async def submit_turn(
        thread_id: str, content: str, client_message_id: str
    ) -> object:
        return {
            "thread_id": thread_id,
            "content": content,
            "client_message_id": client_message_id,
        }

    extensions = ApplicationExtensionService(
        conversation=conversation,
        catalog=catalog,
        loader=loader,
        manager=manager,
        enablements=enablements,
        workspace_key=workspace.key,
        registry=registry,
        submit_turn=submit_turn,
    )
    selected = await extensions.handle(
        CommandIntent(name=CommandName.SKILLS, arguments=("workspace-review",)),
        thread_id=thread.id,
    )
    assert selected.data["skill_mode"] == "workspace-review"
    assert conversation.read_thread(thread.id).thread.skill_mode == "workspace-review"

    configured_thread = conversation.read_thread(thread.id).thread
    turn = conversation.begin_turn(
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

    enabled = await extensions.handle(
        CommandIntent(name=CommandName.MCP, arguments=("enable", "fixture")),
        thread_id=thread.id,
    )
    assert enabled.data["state"] == McpConnectionState.CONFIGURED.value
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
            activity_writer=repositories.tool_activities,
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
    assert conversation.read_thread(thread.id).tool_activities[0].tool_name == (
        "mcp.fixture.echo"
    )
    await manager.aclose()

    restarted = McpManager(
        configs=(fixture_config,),
        workspace_key=workspace.key,
        workspace_trusted=True,
        enablements=enablements,
    )
    restarted_registry = ToolRegistry()
    register_read_tools(restarted_registry)
    restarted_extensions = ApplicationExtensionService(
        conversation=conversation,
        catalog=catalog,
        loader=loader,
        manager=restarted,
        enablements=enablements,
        workspace_key=workspace.key,
        registry=restarted_registry,
        submit_turn=submit_turn,
    )
    assert restarted.status("fixture").state is McpConnectionState.CONFIGURED
    assert restarted_registry.resolve("mcp.fixture.echo") is None
    await restarted_extensions.prepare_turn_extensions()
    assert restarted_registry.resolve("mcp.fixture.echo") is not None
    status_result = await restarted_extensions.handle(
        CommandIntent(name=CommandName.MCP, arguments=("status", "fixture")),
        thread_id=thread.id,
    )
    servers = status_result.data["servers"]
    assert isinstance(servers, list)
    first_server = servers[0]
    assert isinstance(first_server, dict)
    assert first_server["state"] == "connected"
    disabled = await restarted_extensions.handle(
        CommandIntent(name=CommandName.MCP, arguments=("disable", "fixture")),
        thread_id=thread.id,
    )
    assert disabled.data["state"] == "enablement_required"
    assert restarted_registry.resolve("mcp.fixture.echo") is None
    await restarted_extensions.handle(
        CommandIntent(name=CommandName.MCP, arguments=("enable", "fixture")),
        thread_id=thread.id,
    )
    await restarted.aclose()

    changed = fixture_config.model_copy(update={"args": (*fixture_config.args, "x")})
    invalidated = McpManager(
        configs=(changed,),
        workspace_key=workspace.key,
        workspace_trusted=True,
        enablements=enablements,
    )
    invalidated_registry = ToolRegistry()
    register_read_tools(invalidated_registry)
    invalidated_extensions = ApplicationExtensionService(
        conversation=conversation,
        catalog=catalog,
        loader=loader,
        manager=invalidated,
        enablements=enablements,
        workspace_key=workspace.key,
        registry=invalidated_registry,
        submit_turn=submit_turn,
    )
    await invalidated_extensions.prepare_turn_extensions()
    assert invalidated_registry.resolve("mcp.fixture.echo") is None
    assert invalidated_registry.resolve("read_file") is not None


@pytest.mark.asyncio
async def test_skills_select_mode_and_init_submits_a_normal_turn(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills"
    for name in ("init", "review", "debug", "test", "git-workflow"):
        _skill(skill_root, name)
    catalog = discover_skills(
        bundled_root=skill_root,
        user_root=None,
        workspace_root=None,
        workspace_trusted=False,
    )
    database = tmp_path / "application.db"
    conversation = ConversationService(store=SQLiteConversationRepositories(database))
    thread = conversation.create_thread("workspace")
    submitted: list[tuple[str, str]] = []

    async def submit_turn(
        thread_id: str, content: str, client_message_id: str
    ) -> object:
        del client_message_id
        submitted.append((thread_id, content))
        return {"operation_id": "operation"}

    service = ApplicationExtensionService(
        conversation=conversation,
        catalog=catalog,
        loader=SkillLoader(catalog),
        manager=McpManager(
            configs=(),
            workspace_key="workspace",
            workspace_trusted=True,
            enablements=SQLiteMcpEnablementStore(database),
        ),
        enablements=SQLiteMcpEnablementStore(database),
        workspace_key="workspace",
        registry=ToolRegistry(),
        submit_turn=submit_turn,
    )

    listed = await service.handle(
        CommandIntent(name=CommandName.SKILLS),
        thread_id=thread.id,
    )
    picker = await service.handle(
        CommandIntent(name=CommandName.SKILLS),
        thread_id=thread.id,
    )
    initialized = await service.handle(
        CommandIntent(name=CommandName.INIT, arguments=("carefully",)),
        thread_id=thread.id,
    )

    effective = listed.data["effective"]
    assert isinstance(effective, list)
    assert len(effective) == 5
    assert picker.selection is not None
    assert {option.value for option in picker.selection.options} == {
        "auto",
        "off",
        "init",
        "review",
        "debug",
        "test",
        "git-workflow",
    }
    assert initialized.data["skill"] == "init"
    assert conversation.read_thread(thread.id).thread.skill_mode == "init"
    assert submitted == [
        (thread.id, "Initialize durable workspace guidance. carefully")
    ]
