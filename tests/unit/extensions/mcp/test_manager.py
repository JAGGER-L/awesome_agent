from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from awesome_agent.extensions.mcp import (
    McpCallUncertain,
    McpManager,
    McpServerConfig,
    McpSource,
)
from awesome_agent.storage import SQLiteMcpEnablementStore, mcp_config_hash


class FakeClient:
    def __init__(self, server_id: str, *, fails: bool = False) -> None:
        self.server_id = server_id
        self.fails = fails
        self.connect_count = 0
        self.closed = False
        self.fail_call = False
        self.fail_close = False

    async def connect(self) -> None:
        self.connect_count += 1
        if self.fails:
            raise RuntimeError("secret server detail")

    async def list_tools(self) -> tuple[Tool, ...]:
        return (Tool(name="echo", description="echo", inputSchema={"type": "object"}),)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> CallToolResult:
        if self.fail_call:
            raise ConnectionError("lost after dispatch")
        return CallToolResult(
            content=[TextContent(type="text", text=str(arguments.get("text", "")))],
        )

    async def aclose(self) -> None:
        self.closed = True
        if self.fail_close:
            raise RuntimeError("close failed")


@pytest.mark.asyncio
async def test_manager_is_lazy_reuses_sessions_and_isolates_failure(
    tmp_path: Path,
) -> None:
    store = SQLiteMcpEnablementStore(tmp_path / "state.db")
    user = McpServerConfig(
        id="user",
        command="user-server",
        source=McpSource.USER,
        enabled=True,
    )
    project = McpServerConfig(
        id="project",
        command="project-server",
        source=McpSource.WORKSPACE,
    )
    broken = McpServerConfig(
        id="broken",
        command="broken-server",
        source=McpSource.USER,
        enabled=True,
    )
    store.enable("workspace", project.id, mcp_config_hash(project))
    clients: dict[str, FakeClient] = {}

    def factory(config: McpServerConfig) -> FakeClient:
        client = FakeClient(config.id, fails=config.id == "broken")
        clients[config.id] = client
        return client

    manager = McpManager(
        configs=(user, project, broken),
        workspace_key="workspace",
        workspace_trusted=True,
        enablements=store,
        client_factory=factory,
    )

    assert clients == {}
    statuses = await manager.start_enabled()
    assert {status.server_id for status in statuses if status.connected} == {
        "project",
        "user",
    }
    assert manager.tool_names("user") == ("echo",)
    assert manager.tool_names("project") == ("echo",)
    assert manager.tool_names("broken") == ()
    assert "secret server detail" not in str(statuses)

    await manager.start_enabled()
    assert clients["user"].connect_count == 1
    await manager.aclose()
    assert clients["user"].closed is True


@pytest.mark.asyncio
async def test_workspace_trust_and_hash_gate_project_but_not_user(
    tmp_path: Path,
) -> None:
    store = SQLiteMcpEnablementStore(tmp_path / "state.db")
    user = McpServerConfig(
        id="user",
        command="user-server",
        source=McpSource.USER,
        enabled=True,
    )
    project = McpServerConfig(
        id="project",
        command="project-server",
        source=McpSource.WORKSPACE,
    )
    store.enable("workspace", project.id, mcp_config_hash(project))

    created: list[str] = []

    def factory(config: McpServerConfig) -> FakeClient:
        created.append(config.id)
        return FakeClient(config.id)

    manager = McpManager(
        configs=(user, project.model_copy(update={"args": ("changed",)})),
        workspace_key="workspace",
        workspace_trusted=False,
        enablements=store,
        client_factory=factory,
    )

    await manager.start_enabled()

    assert created == ["user"]


@pytest.mark.asyncio
async def test_connection_loss_is_uncertain_and_current_call_is_not_replayed(
    tmp_path: Path,
) -> None:
    config = McpServerConfig(
        id="user",
        command="server",
        source=McpSource.USER,
        enabled=True,
    )
    client = FakeClient("user")
    manager = McpManager(
        configs=(config,),
        workspace_key="workspace",
        workspace_trusted=True,
        enablements=SQLiteMcpEnablementStore(tmp_path / "state.db"),
        client_factory=lambda _: client,
    )
    await manager.start_enabled()
    client.fail_call = True
    client.fail_close = True

    with pytest.raises(McpCallUncertain):
        await manager.call_tool("user", "echo", {"text": "once"})

    assert client.connect_count == 1
    assert client.closed is True
