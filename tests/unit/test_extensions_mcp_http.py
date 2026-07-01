from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.extensions.catalog import publish_catalog
from awesome_agent.extensions.mcp import (
    McpStreamableHttpSource,
    McpStreamableHttpSourceConfig,
    redacted_mcp_auth_payload,
    register_mcp_streamable_http_tools,
)
from awesome_agent.extensions.models import (
    ExtensionAuthConfig,
    ExtensionCatalog,
    ExtensionToolInventoryItem,
)
from awesome_agent.extensions.service import ExtensionDiscoveryService
from awesome_agent.extensions.sources import ExtensionSourceFactory
from awesome_agent.tools.approval import ApprovalPolicy
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.models import ToolInvocation
from awesome_agent.tools.registry import ToolRegistry


async def test_streamable_http_discovery_matches_stdio_inventory() -> None:
    async with _fake_http_client() as client:
        source = McpStreamableHttpSource(
            _http_config(),
            client=client,
        )

        snapshot = await source.discover()

    assert snapshot.source.id == "github"
    assert snapshot.source.type == "mcp_streamable_http"
    assert snapshot.tools[0].name == "mcp.github.create_issue"
    assert snapshot.tools[0].required_capabilities == {"mcp:github:create_issue"}
    assert snapshot.tools[0].input_schema == {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
    }


async def test_streamable_http_factory_publishes_catalog_inventory() -> None:
    async with _fake_http_client() as client:
        source = ExtensionSourceFactory().create(
            {
                "id": "github",
                "type": "mcp_streamable_http",
                "url": "https://mcp.example.test/mcp",
                "trust": "user",
            }
        )
        assert isinstance(source, McpStreamableHttpSource)
        source = McpStreamableHttpSource(_http_config(), client=client)

        catalog = await ExtensionDiscoveryService([source]).publish()

    assert catalog.sources[0].type == "mcp_streamable_http"
    assert catalog.sources[0].health.status == "healthy"
    assert catalog.tools[0].name == "mcp.github.create_issue"


async def test_streamable_http_accepts_sse_json_rpc_messages() -> None:
    async with _fake_http_client(sse=True) as client:
        snapshot = await McpStreamableHttpSource(
            _http_config(),
            client=client,
        ).discover()

    assert snapshot.tools[0].name == "mcp.github.create_issue"


async def test_streamable_http_tool_executes_through_executor() -> None:
    catalog = _catalog_with_http_tool()
    registry = ToolRegistry()
    async with _fake_http_client() as client:
        register_mcp_streamable_http_tools(
            registry,
            config=_http_config(),
            catalog=catalog,
            exposed_tool_names={"mcp.github.create_issue"},
            client=client,
        )
        result = await ToolExecutor(registry, ApprovalPolicy()).execute(
            ToolInvocation(
                tool_name="mcp.github.create_issue",
                agent_id=uuid4(),
                profile="teammate",
                effective_tool_names={"mcp.github.create_issue"},
                capabilities={"mcp:github:create_issue"},
                arguments={"title": "Task 49"},
            )
        )

    assert result.output["status"] == "ok"
    assert result.output["content"] == "created Task 49"
    assert result.output["extension"] == {
        "source_id": "github",
        "catalog_version": catalog.version,
    }
    assert result.output["mcp"] == {"tool": "create_issue", "is_error": False}


async def test_streamable_http_auth_uses_env_without_catalog_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.append(request.headers.get("authorization"))
        return _json_rpc_response(request)

    monkeypatch.setenv("TOKEN_ENV", "fixture-token")
    auth = ExtensionAuthConfig(type="bearer_token_env", env="TOKEN_ENV")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://mcp.example.test",
    ) as client:
        await McpStreamableHttpSource(
            _http_config(auth=auth),
            client=client,
        ).discover()

    payload = redacted_mcp_auth_payload(auth)
    assert captured_headers[0] == "Bearer fixture-token"
    assert payload == {"type": "bearer_token_env", "env": "TOKEN_ENV"}
    assert "secret-token" not in json.dumps(payload).lower()


@asynccontextmanager
async def _fake_http_client(*, sse: bool = False) -> AsyncIterator[httpx.AsyncClient]:
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response(request) if sse else _json_rpc_response(request)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://mcp.example.test",
    )
    try:
        yield client
    finally:
        await client.aclose()


def _json_rpc_response(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    method = body.get("method")
    if method == "initialize":
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                },
            },
        )
    if method == "notifications/initialized":
        return httpx.Response(202, json={"status": "accepted"})
    if method == "tools/list":
        return httpx.Response(200, json=_tools_list_result(body["id"]))
    if method == "tools/call":
        arguments = body.get("params", {}).get("arguments", {})
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "created " + arguments.get("title", ""),
                        }
                    ],
                    "isError": False,
                },
            },
        )
    return httpx.Response(400, json={"error": "unknown method"})


def _sse_response(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    method = body.get("method")
    if method == "initialize":
        payload = {"jsonrpc": "2.0", "id": body["id"], "result": {}}
    elif method == "tools/list":
        payload = _tools_list_result(body["id"])
    else:
        payload = {"jsonrpc": "2.0", "id": body.get("id"), "result": {}}
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=f"event: message\ndata: {json.dumps(payload)}\n\n",
    )


def _tools_list_result(request_id: int) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "tools": [
                {
                    "name": "create_issue",
                    "description": "Create an issue.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}},
                        "required": ["title"],
                    },
                }
            ]
        },
    }


def _http_config(
    *,
    auth: ExtensionAuthConfig | None = None,
) -> McpStreamableHttpSourceConfig:
    return McpStreamableHttpSourceConfig(
        id="github",
        type="mcp_streamable_http",
        url="https://mcp.example.test/mcp",
        trust="user",
        auth=auth,
        discovery_timeout_seconds=2.0,
    )


def _catalog_with_http_tool() -> ExtensionCatalog:
    return publish_catalog(
        sources=[],
        tools=[
            ExtensionToolInventoryItem(
                name="mcp.github.create_issue",
                source_id="github",
                description="Create an issue.",
                risk_level=RiskLevel.MEDIUM,
                required_capabilities={"mcp:github:create_issue"},
                input_schema={
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
            )
        ],
        skills=[],
    )
