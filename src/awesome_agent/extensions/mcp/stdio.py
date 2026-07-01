from __future__ import annotations

import asyncio
import json
import queue
import subprocess
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from typing import IO, Any, cast

from awesome_agent.extensions.models import (
    ExtensionConfigError,
    ExtensionDiscoverySnapshot,
    ExtensionHealthSnapshot,
    ExtensionHealthStatus,
    ExtensionSourceConfig,
    ExtensionSourceSnapshot,
    ExtensionSourceType,
    ExtensionToolInventoryItem,
)


class McpStdioSourceConfig(ExtensionSourceConfig):
    """Config name reserved for the stdio MCP adapter contract."""

_MCP_PROTOCOL_VERSION = "2024-11-05"
_JSON_RPC_VERSION = "2.0"


class McpProtocolError(RuntimeError):
    """Raised when an MCP server returns malformed discovery responses."""


class McpStdioSource:
    def __init__(self, config: ExtensionSourceConfig) -> None:
        if config.command is None:
            raise ExtensionConfigError("mcp_stdio source requires command.")
        self._config = config

    @property
    def source_id(self) -> str:
        return self._config.id

    async def discover(self) -> ExtensionDiscoverySnapshot:
        try:
            tools = await asyncio.to_thread(self._discover_tools_sync)
        except Exception as error:
            if self._config.required:
                raise
            return ExtensionDiscoverySnapshot(
                source=self._source_snapshot(
                    status=ExtensionHealthStatus.UNHEALTHY,
                    detail=_redacted_error(error, command=self._redacted_command()),
                )
            )
        return ExtensionDiscoverySnapshot(
            source=self._source_snapshot(status=ExtensionHealthStatus.HEALTHY),
            tools=tools,
        )

    def _discover_tools_sync(self) -> list[ExtensionToolInventoryItem]:
        command = [cast(str, self._config.command), *self._config.args]
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout = _LineReader(process.stdout)
        deadline = time.monotonic() + self._config.discovery_timeout_seconds
        try:
            self._send_request(
                process,
                request_id=1,
                method="initialize",
                params={
                    "protocolVersion": _MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "awesome-agent-extension-discovery",
                        "version": "0.1.0",
                    },
                },
            )
            self._read_response(stdout, process, request_id=1, deadline=deadline)
            self._send_notification(process, method="notifications/initialized")
            self._send_request(
                process,
                request_id=2,
                method="tools/list",
                params={},
            )
            response = self._read_response(
                stdout,
                process,
                request_id=2,
                deadline=deadline,
            )
            tools = response.get("result", {}).get("tools", [])
            if not isinstance(tools, list):
                raise McpProtocolError("MCP tools/list result must contain tools list.")
            return [self._inventory_item(tool) for tool in tools]
        finally:
            _close_process(process)

    def _send_request(
        self,
        process: subprocess.Popen[str],
        *,
        request_id: int,
        method: str,
        params: Mapping[str, object],
    ) -> None:
        _write_message(
            process,
            {
                "jsonrpc": _JSON_RPC_VERSION,
                "id": request_id,
                "method": method,
                "params": dict(params),
            },
        )

    def _send_notification(
        self,
        process: subprocess.Popen[str],
        *,
        method: str,
    ) -> None:
        _write_message(
            process,
            {
                "jsonrpc": _JSON_RPC_VERSION,
                "method": method,
                "params": {},
            },
        )

    def _read_response(
        self,
        stdout: _LineReader,
        process: subprocess.Popen[str],
        *,
        request_id: int,
        deadline: float,
    ) -> dict[str, Any]:
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining == 0.0:
                raise TimeoutError
            line = stdout.readline(timeout=remaining)
            if line is None:
                exit_code = process.poll()
                if exit_code is None:
                    raise TimeoutError
                raise McpProtocolError(
                    f"MCP process exited before response id {request_id} "
                    f"(exit_code={exit_code})."
                )
            try:
                message = json.loads(line)
            except json.JSONDecodeError as error:
                raise McpProtocolError("MCP process emitted invalid JSON.") from error
            if not isinstance(message, dict):
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise McpProtocolError(
                    f"MCP request {request_id} failed: {_safe_error_code(message)}"
                )
            return cast(dict[str, Any], message)

    def _inventory_item(self, payload: object) -> ExtensionToolInventoryItem:
        if not isinstance(payload, dict):
            raise McpProtocolError("MCP tool entry must be an object.")
        tool_name = payload.get("name")
        if not isinstance(tool_name, str) or not tool_name:
            raise McpProtocolError("MCP tool entry requires a non-empty name.")
        description = payload.get("description", "")
        input_schema = payload.get("inputSchema", {})
        if not isinstance(description, str):
            description = ""
        if not isinstance(input_schema, dict):
            input_schema = {}
        return ExtensionToolInventoryItem(
            name=f"mcp.{self._config.id}.{tool_name}",
            source_id=self._config.id,
            description=description,
            risk_level=self._config.tool_risk_overrides.get(
                tool_name,
                self._config.default_tool_risk_level,
            ),
            required_capabilities=set(
                self._config.tool_capability_overrides.get(
                    tool_name,
                    [f"mcp:{self._config.id}:{tool_name}"],
                )
            ),
            input_schema=dict(input_schema),
        )

    def _source_snapshot(
        self,
        *,
        status: ExtensionHealthStatus,
        detail: str | None = None,
    ) -> ExtensionSourceSnapshot:
        return ExtensionSourceSnapshot(
            id=self._config.id,
            type=ExtensionSourceType.MCP_STDIO,
            trust=self._config.trust,
            health=ExtensionHealthSnapshot(status=status, detail=detail),
        )

    def _redacted_command(self) -> str:
        pieces = [cast(str, self._config.command)]
        for index, value in enumerate(self._config.args):
            pieces.append(
                "<redacted>" if index in self._config.secret_arg_indexes else value
            )
        return " ".join(pieces)


def _write_message(
    process: subprocess.Popen[str],
    message: Mapping[str, object],
) -> None:
    if process.stdin is None:
        raise McpProtocolError("MCP process stdin is unavailable.")
    process.stdin.write(json.dumps(message, separators=(",", ":")))
    process.stdin.write("\n")
    process.stdin.flush()


def _close_process(process: subprocess.Popen[str]) -> None:
    if process.stdin is not None:
        with suppress(BrokenPipeError, OSError):
            process.stdin.close()
    if process.returncode is None:
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)


class _LineReader:
    def __init__(self, stream: IO[str] | None) -> None:
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(
            target=self._read_lines,
            args=(stream,),
            daemon=True,
        )
        self._thread.start()

    def readline(self, *, timeout: float) -> str | None:
        try:
            return self._lines.get(timeout=timeout)
        except queue.Empty:
            return None

    def _read_lines(self, stream: IO[str] | None) -> None:
        if stream is None:
            self._lines.put(None)
            return
        try:
            for line in stream:
                self._lines.put(str(line))
        finally:
            self._lines.put(None)


def _safe_error_code(message: Mapping[str, object]) -> str:
    error = message.get("error")
    if isinstance(error, dict):
        code = error.get("code", "unknown")
        return f"code={code}"
    return "unknown"


def _redacted_error(error: Exception, *, command: str) -> str:
    if isinstance(error, TimeoutError):
        return f"MCP stdio discovery timed out for {command}."
    if isinstance(error, (McpProtocolError, OSError)):
        return f"MCP stdio discovery failed for {command}: {error}"
    return f"MCP stdio discovery failed for {command}: {type(error).__name__}"
