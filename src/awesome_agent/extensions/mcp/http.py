from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from typing import Any, cast

import httpx

from awesome_agent.extensions.mcp.stdio import (
    McpProtocolError,
    McpToolError,
    _bounded_mcp_content,
    _mcp_content_text,
    _source_tool_name,
    _validate_json_object_schema,
)
from awesome_agent.extensions.models import (
    ExtensionAuthConfig,
    ExtensionAuthType,
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

_MCP_PROTOCOL_VERSION = "2024-11-05"
_JSON_RPC_VERSION = "2.0"


class McpStreamableHttpSourceConfig(ExtensionSourceConfig):
    """Config name reserved for the streamable HTTP MCP adapter contract."""


class McpStreamableHttpSource:
    def __init__(
        self,
        config: ExtensionSourceConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if config.url is None:
            raise ExtensionConfigError("mcp_streamable_http source requires url.")
        self._config = config
        self._client = client
        self._semaphore = asyncio.Semaphore(config.max_concurrency)

    @property
    def source_id(self) -> str:
        return self._config.id

    async def discover(self) -> ExtensionDiscoverySnapshot:
        try:
            async with self._semaphore:
                await self._initialize()
                response = await self._request(2, "tools/list", {})
        except Exception as error:
            if self._config.required:
                raise
            return ExtensionDiscoverySnapshot(
                source=self._source_snapshot(
                    status=ExtensionHealthStatus.UNHEALTHY,
                    detail=_redacted_http_error(error, url=self._config.url or ""),
                )
            )
        tools = response.get("result", {}).get("tools", [])
        if not isinstance(tools, list):
            raise McpProtocolError("MCP tools/list result must contain tools list.")
        return ExtensionDiscoverySnapshot(
            source=self._source_snapshot(status=ExtensionHealthStatus.HEALTHY),
            tools=[self._inventory_item(tool) for tool in tools],
        )

    async def _initialize(self) -> None:
        await self._request(
            1,
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "awesome-agent-mcp-http",
                    "version": "0.1.0",
                },
            },
        )
        await self._notification("notifications/initialized")

    async def _request(
        self,
        request_id: int,
        method: str,
        params: Mapping[str, object],
    ) -> dict[str, Any]:
        response = await self._post(
            {
                "jsonrpc": _JSON_RPC_VERSION,
                "id": request_id,
                "method": method,
                "params": dict(params),
            }
        )
        message = _decode_http_json_rpc_response(response, request_id=request_id)
        if "error" in message:
            raise McpProtocolError(f"MCP request {request_id} failed.")
        return message

    async def _notification(self, method: str) -> None:
        await self._post(
            {
                "jsonrpc": _JSON_RPC_VERSION,
                "method": method,
                "params": {},
            }
        )

    async def _post(self, payload: Mapping[str, object]) -> httpx.Response:
        headers = _auth_headers(self._config.auth)
        headers.update(
            {
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
            }
        )
        if self._client is not None:
            response = await self._client.post(
                cast(str, self._config.url),
                json=dict(payload),
                headers=headers,
                timeout=self._config.discovery_timeout_seconds,
            )
        else:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    cast(str, self._config.url),
                    json=dict(payload),
                    headers=headers,
                    timeout=self._config.discovery_timeout_seconds,
                )
        response.raise_for_status()
        return response

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
            type=ExtensionSourceType.MCP_STREAMABLE_HTTP,
            trust=self._config.trust,
            health=ExtensionHealthSnapshot(status=status, detail=detail),
        )


class McpStreamableHttpToolHandler:
    def __init__(
        self,
        *,
        config: ExtensionSourceConfig,
        tool: ExtensionToolInventoryItem,
        catalog_version: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if config.url is None:
            raise ExtensionConfigError("mcp_streamable_http source requires url.")
        self._config = config
        self._tool = tool
        self._catalog_version = catalog_version
        self._mcp_tool_name = _source_tool_name(config.id, tool.name)
        self._source = McpStreamableHttpSource(config, client=client)

    async def __call__(
        self,
        invocation: ToolInvocation,
        _: ProgressCallback | None,
    ) -> ToolResult:
        _validate_json_object_schema(invocation.arguments, self._tool.input_schema)
        async with self._source._semaphore:
            await self._source._initialize()
            response = await self._source._request(
                2,
                "tools/call",
                {
                    "name": self._mcp_tool_name,
                    "arguments": invocation.arguments,
                },
            )
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
            "mcp": {"tool": self._mcp_tool_name, "is_error": False},
            "content": content,
            "truncated": truncated,
        }
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            output["structured_content"] = structured
        return ToolResult(invocation_id=invocation.id, output=output)


def register_mcp_streamable_http_tools(
    registry: ToolRegistry,
    *,
    config: ExtensionSourceConfig,
    catalog: ExtensionCatalog,
    exposed_tool_names: set[str] | frozenset[str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> None:
    for tool in catalog.tools:
        if tool.source_id != config.id or not tool.name.startswith(f"mcp.{config.id}."):
            continue
        if exposed_tool_names is not None and tool.name not in exposed_tool_names:
            continue
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
            McpStreamableHttpToolHandler(
                config=config,
                tool=tool,
                catalog_version=catalog.version,
                client=client,
            ),
        )


def redacted_mcp_auth_payload(
    auth: ExtensionAuthConfig | None,
) -> dict[str, str] | None:
    if auth is None:
        return None
    return {"type": auth.type.value, "env": auth.env}


def _auth_headers(auth: ExtensionAuthConfig | None) -> dict[str, str]:
    if auth is None:
        return {}
    if auth.type is ExtensionAuthType.BEARER_TOKEN_ENV:
        token = os.environ.get(auth.env)
        if not token:
            raise ExtensionConfigError(f"MCP auth token env is not set: {auth.env}")
        return {"authorization": f"Bearer {token}"}
    raise ExtensionConfigError(f"Unsupported MCP auth type: {auth.type.value}")


def _decode_http_json_rpc_response(
    response: httpx.Response,
    *,
    request_id: int,
) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" in content_type:
        return _decode_sse_json_rpc_response(response.text, request_id=request_id)
    decoded = response.json()
    if not isinstance(decoded, dict):
        raise McpProtocolError("MCP HTTP response must be a JSON object.")
    return cast(dict[str, Any], decoded)


def _decode_sse_json_rpc_response(
    text: str,
    *,
    request_id: int,
) -> dict[str, Any]:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        payload = stripped.removeprefix("data:").strip()
        if not payload or payload == "[DONE]":
            continue
        decoded = json.loads(payload)
        if isinstance(decoded, dict) and decoded.get("id") == request_id:
            return cast(dict[str, Any], decoded)
    raise McpProtocolError(f"MCP SSE response missing id {request_id}.")


def _redacted_http_error(error: Exception, *, url: str) -> str:
    if isinstance(error, TimeoutError):
        return f"MCP HTTP discovery timed out for {url}."
    if isinstance(error, (httpx.HTTPError, McpProtocolError, ExtensionConfigError)):
        return f"MCP HTTP discovery failed for {url}: {type(error).__name__}"
    return f"MCP HTTP discovery failed for {url}: {type(error).__name__}"
