from __future__ import annotations

import os
from contextlib import AsyncExitStack
from types import TracebackType

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool

from awesome_agent.extensions.mcp.models import McpServerConfig

_PLATFORM_ENVIRONMENT_NAMES = (
    "PATH",
    "HOME",
    "TMPDIR",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
)


def stdio_environment(config: McpServerConfig) -> dict[str, str]:
    """Build the minimal child environment without storing secret values."""

    names = (*_PLATFORM_ENVIRONMENT_NAMES, *config.env_names)
    return {
        name: value
        for name in dict.fromkeys(names)
        if (value := os.environ.get(name)) is not None
    }


class McpStdioClient:
    """One initialized official-SDK session for a stdio server."""

    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._initialize_count = 0

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    @property
    def initialize_count(self) -> int:
        return self._initialize_count

    async def connect(self) -> None:
        if self._session is not None:
            return
        stack = AsyncExitStack()
        try:
            reader, writer = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=self.config.command,
                        args=list(self.config.args),
                        env=stdio_environment(self.config),
                    )
                )
            )
            session = await stack.enter_async_context(ClientSession(reader, writer))
            await session.initialize()
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session
        self._initialize_count += 1

    async def list_tools(self) -> tuple[Tool, ...]:
        session = self._require_session()
        result = await session.list_tools()
        return tuple(result.tools)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> CallToolResult:
        return await self._require_session().call_tool(name, arguments)

    async def aclose(self) -> None:
        stack = self._stack
        self._stack = None
        self._session = None
        if stack is not None:
            await stack.aclose()

    async def __aenter__(self) -> McpStdioClient:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("MCP stdio session is not connected")
        return self._session
