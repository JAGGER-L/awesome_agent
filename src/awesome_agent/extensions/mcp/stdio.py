from __future__ import annotations

import asyncio
import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from typing import IO, Any, cast

from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionConfigError,
    ExtensionDiscoverySnapshot,
    ExtensionHealthSnapshot,
    ExtensionHealthStatus,
    ExtensionSourceConfig,
    ExtensionSourceSnapshot,
    ExtensionSourceType,
    ExtensionToolInventoryItem,
)
from awesome_agent.tools.models import ToolInvocation, ToolResult, ToolSpec
from awesome_agent.tools.registry import ProgressCallback, ToolRegistry

_MCP_TOOL_RESULT_MAX_CHARS = 30_000


class McpStdioSourceConfig(ExtensionSourceConfig):
    """Config name reserved for the stdio MCP adapter contract."""

_MCP_PROTOCOL_VERSION = "2024-11-05"
_JSON_RPC_VERSION = "2.0"


class McpProtocolError(ValueError):
    """Raised when an MCP server returns malformed discovery responses."""


class McpToolError(ValueError):
    """Raised when an MCP server reports a tool-call error."""


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
            env=_stdio_env(self._config),
        )
        stdout = _LineReader(process.stdout)
        deadline = time.monotonic() + self._config.discovery_timeout_seconds
        try:
            _send_request(
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
            _read_response(stdout, process, request_id=1, deadline=deadline)
            _send_notification(process, method="notifications/initialized")
            _send_request(
                process,
                request_id=2,
                method="tools/list",
                params={},
            )
            response = _read_response(
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


class McpStdioToolHandler:
    def __init__(
        self,
        *,
        config: ExtensionSourceConfig,
        tool: ExtensionToolInventoryItem,
        catalog_version: str,
    ) -> None:
        if config.command is None:
            raise ExtensionConfigError("mcp_stdio source requires command.")
        self._config = config
        self._tool = tool
        self._catalog_version = catalog_version
        self._mcp_tool_name = _source_tool_name(config.id, tool.name)

    async def __call__(
        self,
        invocation: ToolInvocation,
        _: ProgressCallback | None,
    ) -> ToolResult:
        _validate_json_object_schema(
            invocation.arguments,
            self._tool.input_schema,
        )
        output = await asyncio.to_thread(self._call_tool_sync, invocation.arguments)
        return ToolResult(invocation_id=invocation.id, output=output)

    def _call_tool_sync(self, arguments: dict[str, object]) -> dict[str, object]:
        command = [cast(str, self._config.command), *self._config.args]
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_stdio_env(self._config),
        )
        stdout = _LineReader(process.stdout)
        deadline = time.monotonic() + self._config.discovery_timeout_seconds
        try:
            _send_request(
                process,
                request_id=1,
                method="initialize",
                params={
                    "protocolVersion": _MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "awesome-agent-mcp-tool-executor",
                        "version": "0.1.0",
                    },
                },
            )
            _read_response(stdout, process, request_id=1, deadline=deadline)
            _send_notification(process, method="notifications/initialized")
            _send_request(
                process,
                request_id=2,
                method="tools/call",
                params={
                    "name": self._mcp_tool_name,
                    "arguments": arguments,
                },
            )
            response = _read_response(stdout, process, request_id=2, deadline=deadline)
            result = response.get("result", {})
            if not isinstance(result, dict):
                raise McpProtocolError("MCP tools/call result must be an object.")
            if result.get("isError") is True:
                raise McpToolError(_mcp_content_text(result.get("content", []))[:500])
            content, truncated = _bounded_mcp_content(result.get("content", []))
            output: dict[str, object] = {
                "status": "ok",
                "extension": {
                    "source_id": self._config.id,
                    "catalog_version": self._catalog_version,
                },
                "mcp": {
                    "tool": self._mcp_tool_name,
                    "is_error": False,
                },
                "content": content,
                "truncated": truncated,
            }
            structured = result.get("structuredContent")
            if isinstance(structured, dict):
                output["structured_content"] = structured
            return output
        finally:
            _close_process(process)


def register_mcp_stdio_tools(
    registry: ToolRegistry,
    *,
    config: ExtensionSourceConfig,
    catalog: ExtensionCatalog,
    exposed_tool_names: set[str] | frozenset[str] | None = None,
) -> None:
    for tool in catalog.tools:
        if tool.source_id != config.id or not tool.name.startswith(f"mcp.{config.id}."):
            continue
        if exposed_tool_names is not None and tool.name not in exposed_tool_names:
            continue
        handler = McpStdioToolHandler(
            config=config,
            tool=tool,
            catalog_version=catalog.version,
        )
        registry.register(
            ToolSpec(
                name=tool.name,
                description=tool.description,
                risk_level=tool.risk_level,
                required_capabilities=set(tool.required_capabilities),
                sandbox_required=False,
                timeout_seconds=config.discovery_timeout_seconds,
                input_schema=tool.input_schema,
            ),
            handler,
        )


def _write_message(
    process: subprocess.Popen[str],
    message: Mapping[str, object],
) -> None:
    if process.stdin is None:
        raise McpProtocolError("MCP process stdin is unavailable.")
    process.stdin.write(json.dumps(message, separators=(",", ":")))
    process.stdin.write("\n")
    process.stdin.flush()


def _send_request(
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


def _stdio_env(config: ExtensionSourceConfig) -> dict[str, str] | None:
    if config.env is None or not config.env.pass_names:
        return None
    return {
        name: value
        for name in config.env.pass_names
        if (value := os.environ.get(name)) is not None
    }


def _source_tool_name(source_id: str, namespaced_tool_name: str) -> str:
    prefix = f"mcp.{source_id}."
    if not namespaced_tool_name.startswith(prefix):
        raise ExtensionConfigError(
            f"MCP tool {namespaced_tool_name} does not belong to source {source_id}."
        )
    return namespaced_tool_name.removeprefix(prefix)


def _bounded_mcp_content(content: object) -> tuple[str, bool]:
    text = _mcp_content_text(content)
    if len(text) <= _MCP_TOOL_RESULT_MAX_CHARS:
        return text, False
    head = text[:8000]
    tail = text[-3000:]
    return (
        f"{head}\n...[mcp tool output truncated: {len(text)} characters]...\n{tail}",
        True,
    )


def _mcp_content_text(content: object) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            parts.append(item["text"])
        else:
            parts.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
    return "\n".join(parts)


def _validate_json_object_schema(
    arguments: dict[str, object],
    schema: Mapping[str, object],
) -> None:
    if schema.get("type") not in {None, "object"}:
        raise ValueError("MCP tool input schema must be an object schema.")
    required = schema.get("required", [])
    if isinstance(required, list):
        missing = [
            key
            for key in required
            if isinstance(key, str) and key not in arguments
        ]
        if missing:
            raise ValueError(f"MCP tool arguments missing required keys: {missing}")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return
    for key, value in arguments.items():
        property_schema = properties.get(key)
        if isinstance(property_schema, dict):
            _validate_json_schema_value(key, value, property_schema)


def _validate_json_schema_value(
    key: str,
    value: object,
    schema: Mapping[str, object],
) -> None:
    expected = schema.get("type")
    if expected is None:
        return
    expected_types = expected if isinstance(expected, list) else [expected]
    if any(_matches_json_type(value, item) for item in expected_types):
        return
    raise ValueError(f"MCP tool argument {key} does not match schema type {expected}.")


def _matches_json_type(value: object, expected: object) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return True
