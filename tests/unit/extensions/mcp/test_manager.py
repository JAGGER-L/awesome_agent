import asyncio
from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from awesome_agent.extensions.mcp import (
    McpCallUncertain,
    McpConnectionState,
    McpManager,
    McpServerConfig,
    McpSource,
    McpUnavailable,
)
from awesome_agent.storage import SQLiteMcpEnablementStore, mcp_config_hash


class FakeClient:
    def __init__(
        self,
        server_id: str,
        *,
        fails: bool = False,
        tools: tuple[Tool, ...] | None = None,
    ) -> None:
        self.server_id = server_id
        self.fails = fails
        self.connect_count = 0
        self.closed = False
        self.fail_call = False
        self.fail_close = False
        self.call_count = 0
        self.connect_started: asyncio.Event | None = None
        self.connect_release: asyncio.Event | None = None
        self.call_started: asyncio.Event | None = None
        self.call_release: asyncio.Event | None = None
        self._tools = tools or (
            Tool(name="echo", description="echo", inputSchema={"type": "object"}),
        )

    async def connect(self) -> None:
        self.connect_count += 1
        if self.connect_started is not None:
            self.connect_started.set()
        if self.connect_release is not None:
            await self.connect_release.wait()
        if self.fails:
            raise RuntimeError("secret server detail")

    async def list_tools(self) -> tuple[Tool, ...]:
        return self._tools

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> CallToolResult:
        self.call_count += 1
        if self.call_started is not None:
            self.call_started.set()
        if self.call_release is not None:
            await self.call_release.wait()
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
    generation = manager.catalog("user").generation
    client.fail_call = True
    client.fail_close = True

    with pytest.raises(McpCallUncertain):
        await manager.call_tool(
            "user",
            "echo",
            {"text": "once"},
            generation=generation,
        )

    assert client.connect_count == 1
    assert client.closed is True
    assert client.call_count == 1
    assert manager.status("user").state is McpConnectionState.ERROR
    assert manager.tools("user") == ()


@pytest.mark.asyncio
async def test_call_tool_never_connects_lazily(tmp_path: Path) -> None:
    config = McpServerConfig(
        id="user",
        command="server",
        source=McpSource.USER,
        enabled=True,
    )
    created: list[FakeClient] = []

    def factory(_: McpServerConfig) -> FakeClient:
        client = FakeClient("user")
        created.append(client)
        return client

    manager = McpManager(
        configs=(config,),
        workspace_key="workspace",
        workspace_trusted=True,
        enablements=SQLiteMcpEnablementStore(tmp_path / "state.db"),
        client_factory=factory,
    )

    with pytest.raises(McpUnavailable):
        await manager.call_tool("user", "echo", {}, generation=1)

    assert created == []


@pytest.mark.asyncio
async def test_next_turn_preparation_may_reconnect_after_uncertain_call(
    tmp_path: Path,
) -> None:
    config = McpServerConfig(
        id="user",
        command="server",
        source=McpSource.USER,
        enabled=True,
    )
    first = FakeClient("user")
    first.fail_call = True
    second = FakeClient("user")
    clients = [first, second]
    manager = McpManager(
        configs=(config,),
        workspace_key="workspace",
        workspace_trusted=True,
        enablements=SQLiteMcpEnablementStore(tmp_path / "state.db"),
        client_factory=lambda _: clients.pop(0),
    )
    await manager.start_enabled()
    first_generation = manager.catalog("user").generation
    with pytest.raises(McpCallUncertain):
        await manager.call_tool(
            "user",
            "echo",
            {},
            generation=first_generation,
        )

    await manager.start_enabled()

    assert first.connect_count == 1
    assert first.call_count == 1
    assert second.connect_count == 1
    assert manager.status("user").state is McpConnectionState.CONNECTED
    assert manager.catalog("user").generation > first_generation


@pytest.mark.asyncio
async def test_invalid_catalog_is_never_committed_and_is_sanitized(
    tmp_path: Path,
) -> None:
    config = McpServerConfig(
        id="user",
        command="server",
        source=McpSource.USER,
        enabled=True,
    )
    client = FakeClient(
        "user",
        tools=(
            Tool(
                name="unsafe",
                description="unsafe",
                inputSchema={"$ref": "https://secret.invalid/schema"},
            ),
        ),
    )
    manager = McpManager(
        configs=(config,),
        workspace_key="workspace",
        workspace_trusted=True,
        enablements=SQLiteMcpEnablementStore(tmp_path / "state.db"),
        client_factory=lambda _: client,
    )

    await manager.start_enabled()

    status = manager.status("user")
    assert status.state is McpConnectionState.ERROR
    assert status.detail == "MCP server returned an invalid tool catalog."
    assert "secret" not in str(status)
    assert client.closed is True
    assert manager.tools("user") == ()
    with pytest.raises(McpUnavailable):
        manager.catalog("user")


@pytest.mark.asyncio
async def test_restart_invalidates_old_catalog_generation_before_reconnect(
    tmp_path: Path,
) -> None:
    config = McpServerConfig(
        id="user",
        command="server",
        source=McpSource.USER,
        enabled=True,
    )
    clients = [
        FakeClient(
            "user",
            tools=(
                Tool(
                    name="echo",
                    description="first",
                    inputSchema={"type": "object"},
                ),
            ),
        ),
        FakeClient(
            "user",
            tools=(
                Tool(
                    name="echo",
                    description="second",
                    inputSchema={"type": "object"},
                ),
            ),
        ),
    ]
    manager = McpManager(
        configs=(config,),
        workspace_key="workspace",
        workspace_trusted=True,
        enablements=SQLiteMcpEnablementStore(tmp_path / "state.db"),
        client_factory=lambda _: clients.pop(0),
    )
    invalidations: list[str] = []
    manager.bind_catalog_invalidator("user", lambda: invalidations.append("user"))

    await manager.start_enabled()
    first_generation = manager.catalog("user").generation
    await manager.restart("user")
    second_generation = manager.catalog("user").generation

    assert second_generation > first_generation
    assert invalidations == ["user"]
    with pytest.raises(McpUnavailable, match="stale"):
        await manager.call_tool(
            "user",
            "echo",
            {},
            generation=first_generation,
        )


@pytest.mark.asyncio
async def test_restart_removes_stale_catalog_before_waiting_for_reconnect(
    tmp_path: Path,
) -> None:
    config = McpServerConfig(
        id="user",
        command="server",
        source=McpSource.USER,
        enabled=True,
    )
    first = FakeClient("user")
    second = FakeClient("user")
    second.connect_started = asyncio.Event()
    second.connect_release = asyncio.Event()
    clients = [first, second]
    manager = McpManager(
        configs=(config,),
        workspace_key="workspace",
        workspace_trusted=True,
        enablements=SQLiteMcpEnablementStore(tmp_path / "state.db"),
        client_factory=lambda _: clients.pop(0),
    )
    await manager.start_enabled()

    restart = asyncio.create_task(manager.restart("user"))
    await second.connect_started.wait()

    assert first.closed is True
    assert manager.tools("user") == ()
    assert manager.status("user").state is McpConnectionState.CONFIGURED

    second.connect_release.set()
    status = await restart
    assert status.state is McpConnectionState.CONNECTED


@pytest.mark.asyncio
async def test_timeout_invalidates_catalog_without_replaying(tmp_path: Path) -> None:
    config = McpServerConfig(
        id="user",
        command="server",
        source=McpSource.USER,
        enabled=True,
    )
    client = FakeClient("user")
    client.call_started = asyncio.Event()
    client.call_release = asyncio.Event()
    manager = McpManager(
        configs=(config,),
        workspace_key="workspace",
        workspace_trusted=True,
        enablements=SQLiteMcpEnablementStore(tmp_path / "state.db"),
        client_factory=lambda _: client,
        call_timeout_seconds=0.01,
    )
    await manager.start_enabled()
    generation = manager.catalog("user").generation

    with pytest.raises(McpCallUncertain):
        await manager.call_tool("user", "echo", {}, generation=generation)

    assert client.call_count == 1
    assert client.closed is True
    assert manager.status("user").state is McpConnectionState.ERROR
    with pytest.raises(McpUnavailable):
        manager.catalog("user")


@pytest.mark.asyncio
async def test_timeout_never_accepts_late_success_from_backend_that_swallows_cancel(
    tmp_path: Path,
) -> None:
    class LateSuccessClient(FakeClient):
        async def call_tool(
            self,
            name: str,
            arguments: dict[str, object],
        ) -> CallToolResult:
            del name, arguments
            self.call_count += 1
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return CallToolResult(
                    content=[TextContent(type="text", text="late success")]
                )
            raise AssertionError("event wait returned without cancellation")

    config = McpServerConfig(
        id="user",
        command="server",
        source=McpSource.USER,
        enabled=True,
    )
    client = LateSuccessClient("user")
    manager = McpManager(
        configs=(config,),
        workspace_key="workspace",
        workspace_trusted=True,
        enablements=SQLiteMcpEnablementStore(tmp_path / "state.db"),
        client_factory=lambda _: client,
        call_timeout_seconds=0.01,
    )
    await manager.start_enabled()
    generation = manager.catalog("user").generation

    with pytest.raises(McpCallUncertain):
        await manager.call_tool("user", "echo", {}, generation=generation)
    await asyncio.sleep(0)

    assert client.call_count == 1
    assert client.closed is True
    assert manager.status("user").state is McpConnectionState.ERROR
    assert manager.tools("user") == ()


@pytest.mark.asyncio
async def test_cancel_invalidates_catalog_and_preserves_cancellation(
    tmp_path: Path,
) -> None:
    config = McpServerConfig(
        id="user",
        command="server",
        source=McpSource.USER,
        enabled=True,
    )
    client = FakeClient("user")
    client.call_started = asyncio.Event()
    client.call_release = asyncio.Event()
    manager = McpManager(
        configs=(config,),
        workspace_key="workspace",
        workspace_trusted=True,
        enablements=SQLiteMcpEnablementStore(tmp_path / "state.db"),
        client_factory=lambda _: client,
    )
    await manager.start_enabled()
    generation = manager.catalog("user").generation
    task = asyncio.create_task(
        manager.call_tool("user", "echo", {}, generation=generation)
    )
    await client.call_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client.call_count == 1
    assert client.closed is True
    assert manager.status("user").state is McpConnectionState.ERROR
    with pytest.raises(McpUnavailable):
        manager.catalog("user")


@pytest.mark.asyncio
async def test_backend_self_cancellation_is_uncertain_not_caller_cancellation(
    tmp_path: Path,
) -> None:
    class SelfCancellingClient(FakeClient):
        async def call_tool(
            self,
            name: str,
            arguments: dict[str, object],
        ) -> CallToolResult:
            del name, arguments
            self.call_count += 1
            current = asyncio.current_task()
            assert current is not None
            current.cancel()
            await asyncio.sleep(0)
            raise AssertionError("a self-cancelled backend continued running")

    config = McpServerConfig(
        id="user",
        command="server",
        source=McpSource.USER,
        enabled=True,
    )
    client = SelfCancellingClient("user")
    manager = McpManager(
        configs=(config,),
        workspace_key="workspace",
        workspace_trusted=True,
        enablements=SQLiteMcpEnablementStore(tmp_path / "state.db"),
        client_factory=lambda _: client,
    )
    await manager.start_enabled()
    generation = manager.catalog("user").generation

    with pytest.raises(McpCallUncertain):
        await manager.call_tool("user", "echo", {}, generation=generation)

    assert client.call_count == 1
    assert client.closed is True
    assert manager.status("user").state is McpConnectionState.ERROR
    assert manager.tools("user") == ()
