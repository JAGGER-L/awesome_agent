from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from mcp.types import CallToolResult, Tool

from awesome_agent.extensions.mcp.models import (
    McpServerConfig,
    McpSource,
    mcp_config_hash,
)
from awesome_agent.extensions.mcp.stdio import McpStdioClient


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


class McpUnavailable(RuntimeError):
    pass


class McpCallUncertain(RuntimeError):
    """The external call may have executed before the connection was lost."""


class McpManager:
    def __init__(
        self,
        *,
        configs: tuple[McpServerConfig, ...],
        workspace_key: str,
        workspace_trusted: bool,
        enablements: McpEnablementReader,
        client_factory: Callable[[McpServerConfig], McpClient] = McpStdioClient,
    ) -> None:
        self._configs = {config.id: config for config in configs}
        if len(self._configs) != len(configs):
            raise ValueError("MCP server IDs must be unique")
        self._workspace_key = workspace_key
        self._workspace_trusted = workspace_trusted
        self._enablements = enablements
        self._client_factory = client_factory
        self._clients: dict[str, McpClient] = {}
        self._tools: dict[str, tuple[Tool, ...]] = {}
        self._statuses: dict[str, McpServerStatus] = {}

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
        return tuple(tool.name for tool in self._tools.get(server_id, ()))

    def tools(self, server_id: str) -> tuple[Tool, ...]:
        return self._tools.get(server_id, ())

    async def start_enabled(self) -> tuple[McpServerStatus, ...]:
        for config in self._configs.values():
            if not self._is_effective(config):
                self._statuses[config.id] = self._inactive_status(config)
                continue
            if config.id not in self._clients:
                await self._connect_one(config)
        return self.statuses()

    async def restart(self, server_id: str) -> McpServerStatus:
        config = self._config(server_id)
        await self._drop_client(server_id)
        if not self._is_effective(config):
            status = self._inactive_status(config)
            self._statuses[server_id] = status
            return status
        await self._connect_one(config)
        return self.status(server_id)

    async def refresh_enablement(self, server_id: str) -> McpServerStatus:
        config = self._config(server_id)
        await self._drop_client(server_id)
        self._statuses.pop(server_id, None)
        return self._inactive_status(config)

    async def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, object],
    ) -> CallToolResult:
        config = self._config(server_id)
        if not self._is_effective(config):
            raise McpUnavailable(f"MCP server is unavailable: {server_id}")
        if server_id not in self._clients:
            await self._connect_one(config)
        client = self._clients.get(server_id)
        if client is None:
            raise McpUnavailable(f"MCP server failed to connect: {server_id}")
        try:
            return await client.call_tool(tool_name, arguments)
        except Exception as error:
            await self._drop_client(server_id)
            self._statuses[server_id] = McpServerStatus(
                server_id,
                McpConnectionState.ERROR,
                f"Tool connection failed ({type(error).__name__}).",
            )
            raise McpCallUncertain(
                f"MCP call outcome is uncertain: {server_id}.{tool_name}"
            ) from error

    async def aclose(self) -> None:
        for server_id in tuple(self._clients):
            await self._drop_client(server_id)

    async def _connect_one(self, config: McpServerConfig) -> None:
        client = self._client_factory(config)
        try:
            await client.connect()
            tools = await client.list_tools()
        except Exception as error:
            with suppress(Exception):
                await client.aclose()
            self._tools.pop(config.id, None)
            self._statuses[config.id] = McpServerStatus(
                config.id,
                McpConnectionState.ERROR,
                f"Connection failed ({type(error).__name__}).",
            )
            return
        self._clients[config.id] = client
        self._tools[config.id] = tools
        self._statuses[config.id] = McpServerStatus(
            config.id,
            McpConnectionState.CONNECTED,
        )

    async def _drop_client(self, server_id: str) -> None:
        client = self._clients.pop(server_id, None)
        self._tools.pop(server_id, None)
        if client is not None:
            await client.aclose()

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
