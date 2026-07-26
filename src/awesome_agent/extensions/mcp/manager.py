from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from mcp.types import CallToolResult, Tool

from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.extensions.mcp.adapter import McpToolAdapter
from awesome_agent.extensions.mcp.catalog import (
    McpCatalog,
    McpCatalogError,
    compile_mcp_catalog,
)
from awesome_agent.extensions.mcp.errors import McpCallUncertain, McpUnavailable
from awesome_agent.extensions.mcp.models import (
    McpServerConfig,
    McpSource,
    mcp_config_hash,
)
from awesome_agent.extensions.mcp.stdio import McpStdioClient

_MCP_CALL_TIMEOUT_SECONDS = 30.0
_MCP_CATALOG_TIMEOUT_SECONDS = 30.0
_MCP_CLOSE_TIMEOUT_SECONDS = 5.0


class McpClient(Protocol):
    async def connect(self) -> None: ...

    async def list_tools(self) -> tuple[Tool, ...]: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> CallToolResult: ...

    async def aclose(self) -> None: ...


class McpEnablementReader(Protocol):
    def is_enabled(
        self,
        workspace_key: str,
        server_id: str,
        config_hash: str,
    ) -> bool: ...


class McpConnectionState(StrEnum):
    DISABLED = "disabled"
    UNTRUSTED = "untrusted"
    ENABLEMENT_REQUIRED = "enablement_required"
    CONFIGURED = "configured"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class McpServerStatus:
    server_id: str
    state: McpConnectionState
    detail: str | None = None

    @property
    def connected(self) -> bool:
        return self.state is McpConnectionState.CONNECTED


class McpManager:
    def __init__(
        self,
        *,
        configs: tuple[McpServerConfig, ...],
        workspace_key: str,
        workspace_trusted: bool,
        enablements: McpEnablementReader,
        registry: ToolRegistry,
        client_factory: Callable[[McpServerConfig], McpClient] = McpStdioClient,
        call_timeout_seconds: float = _MCP_CALL_TIMEOUT_SECONDS,
        catalog_timeout_seconds: float = _MCP_CATALOG_TIMEOUT_SECONDS,
    ) -> None:
        self._configs = {config.id: config for config in configs}
        if len(self._configs) != len(configs):
            raise ValueError("MCP server IDs must be unique")
        if call_timeout_seconds <= 0:
            raise ValueError("MCP call timeout must be positive")
        if catalog_timeout_seconds <= 0:
            raise ValueError("MCP catalog timeout must be positive")
        self._workspace_key = workspace_key
        self._workspace_trusted = workspace_trusted
        self._enablements = enablements
        self._registry = registry
        self._client_factory = client_factory
        self._call_timeout_seconds = call_timeout_seconds
        self._catalog_timeout_seconds = catalog_timeout_seconds
        self._clients: dict[str, McpClient] = {}
        self._catalogs: dict[str, McpCatalog] = {}
        self._catalog_tasks: dict[str, asyncio.Task[McpCatalog]] = {}
        self._generations = {server_id: 0 for server_id in self._configs}
        self._statuses: dict[str, McpServerStatus] = {}
        self._locks = {server_id: asyncio.Lock() for server_id in self._configs}

    def configs(self) -> tuple[McpServerConfig, ...]:
        return tuple(self._configs.values())

    def config(self, server_id: str) -> McpServerConfig:
        return self._config(server_id)

    def status(self, server_id: str) -> McpServerStatus:
        config = self._config(server_id)
        return self._statuses.get(server_id, self._inactive_status(config))

    def statuses(self) -> tuple[McpServerStatus, ...]:
        return tuple(self.status(server_id) for server_id in self._configs)

    def tool_names(self, server_id: str) -> tuple[str, ...]:
        return tuple(tool.name for tool in self.tools(server_id))

    def tools(self, server_id: str) -> tuple[Tool, ...]:
        catalog = self._catalogs.get(server_id)
        return catalog.tools if catalog is not None else ()

    def catalog(self, server_id: str) -> McpCatalog:
        self._config(server_id)
        try:
            return self._catalogs[server_id]
        except KeyError as error:
            raise McpUnavailable(
                f"MCP server has no active catalog: {server_id}"
            ) from error

    async def start_enabled(self) -> tuple[McpServerStatus, ...]:
        await asyncio.gather(
            *(self._start_one(config) for config in self._configs.values())
        )
        return self.statuses()

    async def restart(self, server_id: str) -> McpServerStatus:
        config = self._config(server_id)
        async with self._locks[server_id]:
            await self._drop_client_locked(server_id)
            self._statuses[server_id] = self._inactive_status(config)
            if not self._is_effective(config):
                return self._statuses[server_id]
            await self._connect_one_locked(config)
            return self.status(server_id)

    async def refresh_enablement(self, server_id: str) -> McpServerStatus:
        config = self._config(server_id)
        async with self._locks[server_id]:
            await self._drop_client_locked(server_id)
            self._statuses.pop(server_id, None)
            return self._inactive_status(config)

    async def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, object],
        *,
        generation: int,
    ) -> CallToolResult:
        config = self._config(server_id)
        if not self._is_effective(config):
            raise McpUnavailable(f"MCP server is unavailable: {server_id}")
        async with self._locks[server_id]:
            client = self._clients.get(server_id)
            catalog = self._catalogs.get(server_id)
            if (
                client is None
                or catalog is None
                or self.status(server_id).state is not McpConnectionState.CONNECTED
            ):
                raise McpUnavailable(f"MCP server is not connected: {server_id}")
            if catalog.generation != generation:
                raise McpUnavailable(f"MCP tool catalog is stale: {server_id}")
            try:
                catalog.resolve(tool_name)
            except McpCatalogError as error:
                raise McpUnavailable(f"MCP tool is unavailable: {server_id}") from error
            call_task = asyncio.create_task(client.call_tool(tool_name, arguments))
            try:
                done, _ = await asyncio.wait(
                    (call_task,),
                    timeout=self._call_timeout_seconds,
                )
                if call_task not in done:
                    call_task.cancel()
                    _detach_task(call_task)
                    await self._invalidate_call_locked(
                        server_id,
                        "MCP tool call timed out; outcome may be uncertain.",
                    )
                    raise McpCallUncertain(
                        f"MCP call outcome is uncertain: {server_id}.{tool_name}"
                    )
                return call_task.result()
            except asyncio.CancelledError as error:
                current_task = asyncio.current_task()
                caller_cancelled = (
                    current_task is not None and current_task.cancelling() > 0
                )
                call_task.cancel()
                _detach_task(call_task)
                await self._invalidate_call_locked(
                    server_id,
                    "MCP tool call was cancelled; outcome may be uncertain.",
                )
                if caller_cancelled:
                    raise
                raise McpCallUncertain(
                    f"MCP call outcome is uncertain: {server_id}.{tool_name}"
                ) from error
            except McpCallUncertain:
                raise
            except Exception as error:
                await self._invalidate_call_locked(
                    server_id,
                    "MCP tool call failed; outcome may be uncertain.",
                )
                raise McpCallUncertain(
                    f"MCP call outcome is uncertain: {server_id}.{tool_name}"
                ) from error

    async def aclose(self) -> None:
        await asyncio.gather(
            *(self._close_one(server_id) for server_id in self._configs)
        )

    async def _start_one(self, config: McpServerConfig) -> None:
        async with self._locks[config.id]:
            if not self._is_effective(config):
                await self._drop_client_locked(config.id)
                self._statuses[config.id] = self._inactive_status(config)
                return
            if config.id not in self._clients:
                await self._connect_one_locked(config)

    async def _close_one(self, server_id: str) -> None:
        async with self._locks[server_id]:
            await self._drop_client_locked(server_id)
            self._statuses[server_id] = self._inactive_status(self._configs[server_id])

    async def _connect_one_locked(self, config: McpServerConfig) -> None:
        previous = self._catalog_tasks.get(config.id)
        if previous is not None:
            if previous.done():
                self._catalog_task_completed(config.id, previous)
            else:
                self._invalidate_catalog(config.id)
                self._statuses[config.id] = McpServerStatus(
                    config.id,
                    McpConnectionState.ERROR,
                    "Previous MCP connection cleanup is still pending.",
                )
                return
        client = self._client_factory(config)
        generation = self._generations[config.id] + 1
        catalog_task = asyncio.create_task(
            self._load_catalog(
                client,
                server_id=config.id,
                generation=generation,
            ),
            name=f"mcp-{config.id}-catalog",
        )
        self._catalog_tasks[config.id] = catalog_task
        catalog_task.add_done_callback(
            lambda task: self._catalog_task_completed(config.id, task)
        )
        try:
            done, _ = await asyncio.wait(
                (catalog_task,),
                timeout=self._catalog_timeout_seconds,
            )
            if catalog_task not in done:
                catalog_task.cancel()
                raise TimeoutError
            catalog = catalog_task.result()
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            caller_cancelled = caller is not None and caller.cancelling() > 0
            catalog_task.cancel()
            await self._close_client(client)
            self._invalidate_catalog(config.id)
            self._statuses[config.id] = McpServerStatus(
                config.id,
                McpConnectionState.ERROR,
                (
                    "MCP server connection was cancelled."
                    if caller_cancelled
                    else "MCP server connection failed."
                ),
            )
            if caller_cancelled:
                raise
            return
        except TimeoutError:
            await self._close_client(client)
            self._invalidate_catalog(config.id)
            self._statuses[config.id] = McpServerStatus(
                config.id,
                McpConnectionState.ERROR,
                "MCP server connection timed out.",
            )
            return
        except McpCatalogError:
            await self._close_client(client)
            self._invalidate_catalog(config.id)
            self._statuses[config.id] = McpServerStatus(
                config.id,
                McpConnectionState.ERROR,
                "MCP server returned an invalid tool catalog.",
            )
            return
        except Exception:
            await self._close_client(client)
            self._invalidate_catalog(config.id)
            self._statuses[config.id] = McpServerStatus(
                config.id,
                McpConnectionState.ERROR,
                "MCP server connection failed.",
            )
            return
        try:
            registered = McpToolAdapter(self, config.id).registered_tools(catalog)
            self._registry.replace_namespace(f"mcp.{config.id}", registered)
            self._generations[config.id] = generation
            self._clients[config.id] = client
            self._catalogs[config.id] = catalog
            self._statuses[config.id] = McpServerStatus(
                config.id,
                McpConnectionState.CONNECTED,
            )
        except Exception:
            self._clients.pop(config.id, None)
            self._invalidate_catalog(config.id)
            self._statuses[config.id] = McpServerStatus(
                config.id,
                McpConnectionState.ERROR,
                "MCP server catalog could not be published.",
            )
            await self._close_client(client)
            return

    def _catalog_task_completed(
        self,
        server_id: str,
        task: asyncio.Task[McpCatalog],
    ) -> None:
        if self._catalog_tasks.get(server_id) is task:
            self._catalog_tasks.pop(server_id, None)
        with suppress(Exception, asyncio.CancelledError):
            task.result()

    @staticmethod
    async def _load_catalog(
        client: McpClient,
        *,
        server_id: str,
        generation: int,
    ) -> McpCatalog:
        await client.connect()
        tools = await client.list_tools()
        return compile_mcp_catalog(
            tools,
            server_id=server_id,
            generation=generation,
        )

    async def _invalidate_call_locked(self, server_id: str, detail: str) -> None:
        client = self._clients.pop(server_id, None)
        self._invalidate_catalog(server_id)
        self._statuses[server_id] = McpServerStatus(
            server_id,
            McpConnectionState.ERROR,
            detail,
        )
        if client is not None:
            await self._close_client(client)

    async def _drop_client_locked(self, server_id: str) -> None:
        client = self._clients.pop(server_id, None)
        self._invalidate_catalog(server_id)
        if client is not None:
            await self._close_client(client)

    def _invalidate_catalog(self, server_id: str) -> None:
        self._catalogs.pop(server_id, None)
        self._generations[server_id] += 1
        self._registry.remove_namespace(f"mcp.{server_id}")

    @staticmethod
    async def _close_client(client: McpClient) -> None:
        close_task = asyncio.create_task(client.aclose())
        try:
            done, _ = await asyncio.wait(
                (close_task,),
                timeout=_MCP_CLOSE_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            close_task.cancel()
            _detach_task(close_task)
            raise
        if close_task not in done:
            close_task.cancel()
            _detach_task(close_task)
            return
        with suppress(Exception, asyncio.CancelledError):
            close_task.result()

    def _config(self, server_id: str) -> McpServerConfig:
        try:
            return self._configs[server_id]
        except KeyError as error:
            raise McpUnavailable(f"Unknown MCP server: {server_id}") from error

    def _is_effective(self, config: McpServerConfig) -> bool:
        if config.source is McpSource.USER:
            return config.enabled
        return self._workspace_trusted and self._enablements.is_enabled(
            self._workspace_key,
            config.id,
            mcp_config_hash(config),
        )

    def _inactive_status(self, config: McpServerConfig) -> McpServerStatus:
        if config.source is McpSource.USER:
            state = (
                McpConnectionState.CONFIGURED
                if config.enabled
                else McpConnectionState.DISABLED
            )
        elif not self._workspace_trusted:
            state = McpConnectionState.UNTRUSTED
        elif self._enablements.is_enabled(
            self._workspace_key,
            config.id,
            mcp_config_hash(config),
        ):
            state = McpConnectionState.CONFIGURED
        else:
            state = McpConnectionState.ENABLEMENT_REQUIRED
        return McpServerStatus(config.id, state)


def _detach_task(task: asyncio.Task[object]) -> None:
    """Consume a late terminal result without ever accepting it as call success."""

    def consume(completed: asyncio.Task[object]) -> None:
        with suppress(Exception, asyncio.CancelledError):
            completed.result()

    task.add_done_callback(consume)
