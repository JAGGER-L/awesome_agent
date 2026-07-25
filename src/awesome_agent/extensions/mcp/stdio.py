from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack, suppress
from types import TracebackType

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool

from awesome_agent.extensions.mcp.catalog import (
    MAX_MCP_CATALOG_BYTES,
    MAX_MCP_TOOLS,
    McpCatalogError,
    mcp_tool_contract_size,
)
from awesome_agent.extensions.mcp.models import McpServerConfig

_MCP_INITIALIZE_TIMEOUT_SECONDS = 30.0
_MCP_LIST_TIMEOUT_SECONDS = 30.0
_MCP_CLOSE_TIMEOUT_SECONDS = 5.0
_MAX_MCP_CATALOG_PAGES = 128

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

    def __init__(
        self,
        config: McpServerConfig,
        *,
        initialize_timeout_seconds: float = _MCP_INITIALIZE_TIMEOUT_SECONDS,
        list_timeout_seconds: float = _MCP_LIST_TIMEOUT_SECONDS,
        close_timeout_seconds: float = _MCP_CLOSE_TIMEOUT_SECONDS,
    ) -> None:
        if initialize_timeout_seconds <= 0:
            raise ValueError("MCP initialize timeout must be positive")
        if list_timeout_seconds <= 0:
            raise ValueError("MCP list timeout must be positive")
        if close_timeout_seconds <= 0:
            raise ValueError("MCP close timeout must be positive")
        self.config = config
        self._initialize_timeout_seconds = initialize_timeout_seconds
        self._list_timeout_seconds = list_timeout_seconds
        self._close_timeout_seconds = close_timeout_seconds
        self._session: ClientSession | None = None
        self._initialize_count = 0
        self._owner_task: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[None] | None = None
        self._close_requested: asyncio.Event | None = None

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    @property
    def initialize_count(self) -> int:
        return self._initialize_count

    async def connect(self) -> None:
        owner = self._owner_task
        if owner is not None and owner.done():
            self._clear_owner(owner)
            owner = None
        ready: asyncio.Future[None]
        close_requested: asyncio.Event
        if owner is None:
            owner, ready, close_requested = self._start_owner()
        else:
            stored_ready = self._ready
            stored_close_requested = self._close_requested
            if stored_ready is None or stored_close_requested is None:
                raise RuntimeError("MCP stdio lifecycle state is invalid")
            ready = stored_ready
            close_requested = stored_close_requested
        if close_requested.is_set():
            raise RuntimeError("MCP stdio session is closing")
        try:
            async with asyncio.timeout(self._initialize_timeout_seconds):
                await asyncio.shield(ready)
        except TimeoutError as error:
            await self._terminate_owner(owner, close_requested)
            raise TimeoutError("MCP stdio initialization timed out") from error
        except asyncio.CancelledError:
            await self._terminate_owner(owner, close_requested)
            raise
        except BaseException:
            if owner.done():
                self._clear_owner(owner)
            raise
        if close_requested.is_set() or self._session is None:
            if owner.done():
                self._clear_owner(owner)
            raise RuntimeError("MCP stdio session closed during connection")

    async def list_tools(self) -> tuple[Tool, ...]:
        session = self._require_session()
        try:
            async with asyncio.timeout(self._list_timeout_seconds):
                return await self._list_all_tools(session)
        except TimeoutError as error:
            await self.aclose()
            raise TimeoutError("MCP stdio tool catalog request timed out") from error
        except asyncio.CancelledError:
            await self.aclose()
            raise

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> CallToolResult:
        return await self._require_session().call_tool(name, arguments)

    async def aclose(self) -> None:
        owner = self._owner_task
        close_requested = self._close_requested
        connected = self._session is not None
        self._session = None
        if owner is None or close_requested is None:
            return
        already_closing = close_requested.is_set()
        close_requested.set()
        if not connected and not already_closing and not owner.done():
            owner.cancel()
        try:
            async with asyncio.timeout(self._close_timeout_seconds):
                await asyncio.shield(owner)
        except TimeoutError:
            owner.cancel()
            _detach_task(owner)
        finally:
            if owner.done():
                self._clear_owner(owner)

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

    async def _list_all_tools(self, session: ClientSession) -> tuple[Tool, ...]:
        tools: list[Tool] = []
        seen_cursors: set[str] = set()
        cursor: str | None = None
        catalog_bytes = 0
        for _ in range(_MAX_MCP_CATALOG_PAGES):
            result = await session.list_tools(cursor=cursor)
            for tool in result.tools:
                tools.append(tool)
                if len(tools) > MAX_MCP_TOOLS:
                    raise McpCatalogError("MCP catalog exceeds the tool limit")
                catalog_bytes += mcp_tool_contract_size(tool)
                if catalog_bytes > MAX_MCP_CATALOG_BYTES:
                    raise McpCatalogError("MCP catalog exceeds the size limit")
            next_cursor = result.nextCursor
            if next_cursor is None:
                return tuple(tools)
            if next_cursor in seen_cursors:
                raise McpCatalogError("MCP catalog contains a cursor cycle")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise McpCatalogError("MCP catalog exceeds the page limit")

    async def _terminate_owner(
        self,
        owner: asyncio.Task[None],
        close_requested: asyncio.Event,
    ) -> None:
        self._session = None
        close_requested.set()
        if not owner.done():
            owner.cancel()
        try:
            async with asyncio.timeout(self._close_timeout_seconds):
                await asyncio.shield(owner)
        except TimeoutError:
            owner.cancel()
            _detach_task(owner)
        finally:
            if owner.done():
                self._clear_owner(owner)

    def _start_owner(
        self,
    ) -> tuple[asyncio.Task[None], asyncio.Future[None], asyncio.Event]:
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[None] = loop.create_future()
        ready.add_done_callback(_consume_future_result)
        close_requested = asyncio.Event()
        owner = asyncio.create_task(
            self._run_lifecycle(ready, close_requested),
            name=f"mcp-stdio-{self.config.id}-lifecycle",
        )
        owner.add_done_callback(self._owner_finished)
        self._owner_task = owner
        self._ready = ready
        self._close_requested = close_requested
        return owner, ready, close_requested

    async def _run_lifecycle(
        self,
        ready: asyncio.Future[None],
        close_requested: asyncio.Event,
    ) -> None:
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
        except BaseException as error:
            self._session = None
            with suppress(BaseException):
                await stack.aclose()
            if not ready.done():
                ready.set_exception(error)
            return

        self._session = session
        self._initialize_count += 1
        if not ready.done():
            ready.set_result(None)
        try:
            await close_requested.wait()
        finally:
            self._session = None
            await stack.aclose()

    def _clear_owner(self, owner: asyncio.Task[None]) -> None:
        if self._owner_task is not owner:
            return
        self._owner_task = None
        self._ready = None
        self._close_requested = None

    def _owner_finished(self, owner: asyncio.Task[None]) -> None:
        _consume_future_result(owner)
        self._clear_owner(owner)


def _consume_future_result(future: asyncio.Future[None]) -> None:
    with suppress(Exception, asyncio.CancelledError):
        future.result()


def _detach_task(task: asyncio.Task[object]) -> None:
    def consume(completed: asyncio.Task[object]) -> None:
        with suppress(Exception, asyncio.CancelledError):
            completed.result()

    task.add_done_callback(consume)
