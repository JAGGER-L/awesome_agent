from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp.types import ListToolsResult, TextContent, Tool

import awesome_agent.extensions.mcp.stdio as stdio_module
from awesome_agent.extensions.mcp import (
    McpServerConfig,
    McpSource,
    McpStdioClient,
    stdio_environment,
)


class _ControlledSdk:
    def __init__(self, *, initialize_error: BaseException | None = None) -> None:
        self.initialize_error = initialize_error
        self.initialize_started = asyncio.Event()
        self.initialize_release = asyncio.Event()
        self.session_exit_started = asyncio.Event()
        self.close_release = asyncio.Event()
        self.owner_task: asyncio.Task[object] | None = None
        self.session_exit_task: asyncio.Task[object] | None = None
        self.stdio_exit_task: asyncio.Task[object] | None = None
        self.list_tools_handler: Callable[[str | None], ListToolsResult] = (
            lambda cursor: ListToolsResult(tools=[])
        )
        self.list_tools_cursors: list[str | None] = []
        self.list_tools_started = asyncio.Event()
        self.list_tools_release = asyncio.Event()
        self.list_tools_release.set()

    @asynccontextmanager
    async def stdio_client(
        self,
        parameters: object,
    ) -> AsyncIterator[tuple[object, object]]:
        del parameters
        owner = asyncio.current_task()
        assert owner is not None
        self.owner_task = owner
        try:
            yield object(), object()
        finally:
            self.stdio_exit_task = asyncio.current_task()

    def session_factory(self, reader: object, writer: object) -> _ControlledSession:
        del reader, writer
        return _ControlledSession(self)


class _ControlledSession:
    def __init__(self, sdk: _ControlledSdk) -> None:
        self._sdk = sdk

    async def __aenter__(self) -> _ControlledSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args
        self._sdk.session_exit_task = asyncio.current_task()
        self._sdk.session_exit_started.set()
        await self._sdk.close_release.wait()

    async def initialize(self) -> None:
        self._sdk.initialize_started.set()
        await self._sdk.initialize_release.wait()
        if self._sdk.initialize_error is not None:
            raise self._sdk.initialize_error

    async def list_tools(self, cursor: str | None = None) -> ListToolsResult:
        self._sdk.list_tools_cursors.append(cursor)
        self._sdk.list_tools_started.set()
        await self._sdk.list_tools_release.wait()
        return self._sdk.list_tools_handler(cursor)


def _controlled_client(
    monkeypatch: pytest.MonkeyPatch,
    sdk: _ControlledSdk,
    *,
    initialize_timeout_seconds: float = 30.0,
    list_timeout_seconds: float = 30.0,
    close_timeout_seconds: float = 5.0,
) -> McpStdioClient:
    monkeypatch.setattr(stdio_module, "stdio_client", sdk.stdio_client)
    monkeypatch.setattr(stdio_module, "ClientSession", sdk.session_factory)
    config = McpServerConfig(
        id="controlled",
        command="server",
        source=McpSource.USER,
        enabled=True,
    )
    if (
        initialize_timeout_seconds == 30.0
        and list_timeout_seconds == 30.0
        and close_timeout_seconds == 5.0
    ):
        return McpStdioClient(config)
    return McpStdioClient(
        config,
        initialize_timeout_seconds=initialize_timeout_seconds,
        list_timeout_seconds=list_timeout_seconds,
        close_timeout_seconds=close_timeout_seconds,
    )


def _tool(name: str, *, payload_size: int = 0) -> Tool:
    return Tool(
        name=name,
        inputSchema={"type": "string", "default": "x" * payload_size},
    )


@pytest.mark.asyncio
async def test_official_sdk_stdio_initializes_lists_calls_and_closes() -> None:
    fixture = Path(__file__).parents[3] / "fixtures" / "mcp_stdio_server.py"
    config = McpServerConfig(
        id="fixture",
        command=sys.executable,
        args=("-u", str(fixture)),
        source=McpSource.USER,
        enabled=True,
    )

    async with McpStdioClient(config) as client:
        await client.connect()
        tools = await client.list_tools()
        echoed = await client.call_tool("echo", {"text": "hello"})
        failed = await client.call_tool("fail", {})

        assert client.initialize_count == 1
        assert {tool.name for tool in tools} == {"echo", "fail"}
        assert echoed.isError is not True
        assert isinstance(echoed.content[0], TextContent)
        assert echoed.content[0].text == "hello"
        assert failed.isError is True

    assert client.is_connected is False


@pytest.mark.asyncio
async def test_stdio_collects_every_tool_page_before_returning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _ControlledSdk()
    sdk.initialize_release.set()
    sdk.close_release.set()
    sdk.list_tools_handler = lambda cursor: (
        ListToolsResult(tools=[_tool("first")], nextCursor="page-2")
        if cursor is None
        else ListToolsResult(tools=[_tool("second")])
    )
    client = _controlled_client(monkeypatch, sdk)

    async with client:
        tools = await client.list_tools()

    assert tuple(item.name for item in tools) == ("first", "second")
    assert sdk.list_tools_cursors == [None, "page-2"]


@pytest.mark.asyncio
async def test_stdio_rejects_cursor_cycles_and_page_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _ControlledSdk()
    sdk.initialize_release.set()
    sdk.close_release.set()
    sdk.list_tools_handler = lambda cursor: ListToolsResult(
        tools=[],
        nextCursor="cycle",
    )
    client = _controlled_client(monkeypatch, sdk)

    async with client:
        with pytest.raises(ValueError, match="cursor cycle"):
            await client.list_tools()

    page_sdk = _ControlledSdk()
    page_sdk.initialize_release.set()
    page_sdk.close_release.set()
    page_sdk.list_tools_handler = lambda cursor: ListToolsResult(
        tools=[],
        nextCursor=str(int(cursor or "0") + 1),
    )
    page_client = _controlled_client(monkeypatch, page_sdk)

    async with page_client:
        with pytest.raises(ValueError, match="page limit"):
            await page_client.list_tools()


@pytest.mark.asyncio
async def test_stdio_accepts_catalog_at_page_and_tool_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _ControlledSdk()
    sdk.initialize_release.set()
    sdk.close_release.set()

    def page(cursor: str | None) -> ListToolsResult:
        index = int(cursor or "0")
        return ListToolsResult(
            tools=[_tool(f"tool_{index}")],
            nextCursor=None if index == 127 else str(index + 1),
        )

    sdk.list_tools_handler = page
    client = _controlled_client(monkeypatch, sdk)

    async with client:
        tools = await client.list_tools()

    assert len(tools) == 128
    assert sdk.list_tools_cursors[0] is None
    assert sdk.list_tools_cursors[-1] == "127"


@pytest.mark.asyncio
async def test_stdio_rejects_tool_and_total_catalog_limits_during_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _ControlledSdk()
    sdk.initialize_release.set()
    sdk.close_release.set()
    sdk.list_tools_handler = lambda cursor: (
        ListToolsResult(
            tools=[_tool(f"tool_{index}") for index in range(100)],
            nextCursor="rest",
        )
        if cursor is None
        else ListToolsResult(
            tools=[_tool(f"tool_{index}") for index in range(100, 129)]
        )
    )
    client = _controlled_client(monkeypatch, sdk)

    async with client:
        with pytest.raises(ValueError, match="tool limit"):
            await client.list_tools()

    bytes_sdk = _ControlledSdk()
    bytes_sdk.initialize_release.set()
    bytes_sdk.close_release.set()
    bytes_sdk.list_tools_handler = lambda cursor: (
        ListToolsResult(
            tools=[_tool(f"large_{index}", payload_size=220_000) for index in range(3)],
            nextCursor="rest",
        )
        if cursor is None
        else ListToolsResult(
            tools=[
                _tool(f"large_{index}", payload_size=220_000) for index in range(3, 5)
            ]
        )
    )
    bytes_client = _controlled_client(monkeypatch, bytes_sdk)

    async with bytes_client:
        with pytest.raises(ValueError, match="size limit"):
            await bytes_client.list_tools()


@pytest.mark.asyncio
async def test_stdio_initialize_timeout_cancels_and_reaps_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _ControlledSdk()
    sdk.close_release.set()
    client = _controlled_client(
        monkeypatch,
        sdk,
        initialize_timeout_seconds=0.01,
        close_timeout_seconds=0.05,
    )

    with pytest.raises(TimeoutError, match="initialization timed out"):
        await client.connect()

    assert sdk.owner_task is not None
    assert sdk.owner_task.done()
    assert sdk.session_exit_task is sdk.owner_task
    assert sdk.stdio_exit_task is sdk.owner_task
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_stdio_list_timeout_closes_session_and_reaps_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _ControlledSdk()
    sdk.initialize_release.set()
    sdk.list_tools_release.clear()
    sdk.close_release.set()
    client = _controlled_client(
        monkeypatch,
        sdk,
        list_timeout_seconds=0.01,
        close_timeout_seconds=0.05,
    )
    await client.connect()

    with pytest.raises(TimeoutError, match="catalog request timed out"):
        await client.list_tools()

    assert sdk.owner_task is not None
    assert sdk.owner_task.done()
    assert sdk.session_exit_task is sdk.owner_task
    assert sdk.stdio_exit_task is sdk.owner_task
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_stdio_close_timeout_cancels_lifecycle_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _ControlledSdk()
    sdk.initialize_release.set()
    client = _controlled_client(
        monkeypatch,
        sdk,
        close_timeout_seconds=0.01,
    )
    await client.connect()

    await asyncio.wait_for(client.aclose(), timeout=0.1)
    await asyncio.sleep(0)

    assert sdk.owner_task is not None
    assert sdk.owner_task.done()
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_stdio_lifecycle_owner_closes_contexts_from_separate_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _ControlledSdk()
    sdk.initialize_release.set()
    sdk.close_release.set()
    client = _controlled_client(monkeypatch, sdk)
    await client.connect()

    close_caller = asyncio.create_task(client.aclose())
    await close_caller

    assert sdk.owner_task is not None
    assert sdk.owner_task is not close_caller
    assert sdk.session_exit_task is sdk.owner_task
    assert sdk.stdio_exit_task is sdk.owner_task
    assert sdk.owner_task.done()
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_cancelling_close_waiter_does_not_cancel_lifecycle_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _ControlledSdk()
    sdk.initialize_release.set()
    client = _controlled_client(monkeypatch, sdk)
    await client.connect()
    assert sdk.owner_task is not None

    close_caller = asyncio.create_task(client.aclose())
    await sdk.session_exit_started.wait()
    close_caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close_caller

    assert sdk.owner_task.done() is False
    sdk.close_release.set()
    await client.aclose()

    assert sdk.owner_task.done()
    assert sdk.session_exit_task is sdk.owner_task
    assert sdk.stdio_exit_task is sdk.owner_task


@pytest.mark.asyncio
async def test_cancelling_connect_requests_lifecycle_owner_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _ControlledSdk()
    sdk.close_release.set()
    client = _controlled_client(monkeypatch, sdk)
    connect_caller = asyncio.create_task(client.connect())
    await sdk.initialize_started.wait()
    assert sdk.owner_task is not None

    connect_caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await connect_caller
    sdk.initialize_release.set()
    await client.aclose()

    assert sdk.owner_task.done()
    assert sdk.session_exit_task is sdk.owner_task
    assert sdk.stdio_exit_task is sdk.owner_task
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_connect_error_is_reported_after_owner_cleans_contexts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _ControlledSdk(initialize_error=RuntimeError("initialize failed"))
    sdk.initialize_release.set()
    sdk.close_release.set()
    client = _controlled_client(monkeypatch, sdk)

    with pytest.raises(RuntimeError, match="initialize failed"):
        await client.connect()

    assert sdk.owner_task is not None
    assert sdk.owner_task.done()
    assert sdk.session_exit_task is sdk.owner_task
    assert sdk.stdio_exit_task is sdk.owner_task
    assert client.is_connected is False
    await client.aclose()


def test_stdio_environment_is_minimal_declared_and_secret_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWESOME_MCP_SECRET", "top-secret")
    monkeypatch.setenv("UNDECLARED_SECRET", "must-not-pass")
    config = McpServerConfig(
        id="fixture",
        command="python",
        env_names=("AWESOME_MCP_SECRET",),
        source=McpSource.WORKSPACE,
    )

    environment = stdio_environment(config)
    serialized = config.model_dump_json()

    assert environment["AWESOME_MCP_SECRET"] == "top-secret"
    assert "UNDECLARED_SECRET" not in environment
    assert "top-secret" not in serialized
    if "PATH" in os.environ:
        assert environment["PATH"] == os.environ["PATH"]
