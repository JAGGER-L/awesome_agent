from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp.types import TextContent

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


def _controlled_client(
    monkeypatch: pytest.MonkeyPatch,
    sdk: _ControlledSdk,
) -> McpStdioClient:
    monkeypatch.setattr(stdio_module, "stdio_client", sdk.stdio_client)
    monkeypatch.setattr(stdio_module, "ClientSession", sdk.session_factory)
    return McpStdioClient(
        McpServerConfig(
            id="controlled",
            command="server",
            source=McpSource.USER,
            enabled=True,
        )
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
