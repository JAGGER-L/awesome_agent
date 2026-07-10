import os
import sys
from pathlib import Path

import pytest
from mcp.types import TextContent

from awesome_agent.extensions.mcp import (
    McpServerConfig,
    McpSource,
    McpStdioClient,
    stdio_environment,
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
