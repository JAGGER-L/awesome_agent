from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.extensions.catalog import publish_catalog
from awesome_agent.extensions.mcp import McpStdioSource, McpStdioSourceConfig
from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionToolInventoryItem,
)
from awesome_agent.extensions.service import ExtensionDiscoveryService
from awesome_agent.extensions.sources import ExtensionSourceFactory
from awesome_agent.runtime.capabilities import CapabilityResolver
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
)
from awesome_agent.runtime.tool_exposure import resolve_tool_exposure


def test_mcp_stdio_discovery_normalizes_tools(tmp_path: Path) -> None:
    fake_server = _fake_mcp_server(tmp_path)
    source = McpStdioSource(
        McpStdioSourceConfig(
            id="playwright",
            type="mcp_stdio",
            command=sys.executable,
            args=[str(fake_server)],
            trust="user",
            tool_capability_overrides={"open_page": ["browser:control"]},
            tool_risk_overrides={"open_page": RiskLevel.LOW},
        )
    )

    snapshot = asyncio.run(source.discover())

    assert snapshot.source.id == "playwright"
    assert snapshot.source.type == "mcp_stdio"
    assert snapshot.source.health.status == "healthy"
    assert snapshot.tools[0].name == "mcp.playwright.open_page"
    assert snapshot.tools[0].source_id == "playwright"
    assert snapshot.tools[0].required_capabilities == {"browser:control"}
    assert snapshot.tools[0].risk_level is RiskLevel.LOW
    assert snapshot.tools[0].input_schema == {
        "type": "object",
        "properties": {"url": {"type": "string"}},
    }


def test_mcp_stdio_factory_publishes_catalog_inventory(tmp_path: Path) -> None:
    fake_server = _fake_mcp_server(tmp_path)
    source = ExtensionSourceFactory().create(
        {
            "id": "playwright",
            "type": "mcp_stdio",
            "command": sys.executable,
            "args": [str(fake_server)],
            "trust": "user",
            "tool_capability_overrides": {
                "open_page": ["browser:control"],
            },
        }
    )

    catalog = asyncio.run(ExtensionDiscoveryService([source]).publish())

    assert catalog.sources[0].type == "mcp_stdio"
    assert catalog.sources[0].health.status == "healthy"
    assert catalog.tools[0].name == "mcp.playwright.open_page"


def test_discovered_mcp_tool_is_hidden_without_grant() -> None:
    catalog = _catalog_with_mcp_tool("mcp.playwright.open_page")
    assignment = _assignment(allowed_tools=[])
    policy = CapabilityResolver().resolve_team_assignment(
        assignment,
        requested_tools=["mcp.playwright.open_page"],
        catalog=catalog,
    )

    exposure = resolve_tool_exposure(policy=policy, catalog=catalog)

    assert not exposure.allows("mcp.playwright.open_page")
    assert exposure.denied_reason("mcp.playwright.open_page") == "not_assigned"


def test_optional_mcp_stdio_failure_records_redacted_unhealthy_source(
    tmp_path: Path,
) -> None:
    source = McpStdioSource(
        McpStdioSourceConfig(
            id="broken",
            type="mcp_stdio",
            command=sys.executable,
            args=[str(tmp_path / "missing.py"), "top-secret-token"],
            trust="user",
            required=False,
            secret_arg_indexes={1},
        )
    )

    catalog = asyncio.run(ExtensionDiscoveryService([source]).publish())

    assert catalog.sources[0].health.status == "unhealthy"
    assert catalog.tools == []
    detail = catalog.sources[0].health.detail or ""
    assert "top-secret-token" not in detail
    assert "<redacted>" in detail


def _fake_mcp_server(tmp_path: Path) -> Path:
    server = tmp_path / "fake_mcp_server.py"
    server.write_text(
        """
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        sys.stdout.write(json.dumps({
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-mcp", "version": "1"}
            }
        }) + "\\n")
        sys.stdout.flush()
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        sys.stdout.write(json.dumps({
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {
                "tools": [{
                    "name": "open_page",
                    "description": "Open a browser page.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"url": {"type": "string"}}
                    }
                }]
            }
        }) + "\\n")
        sys.stdout.flush()
""",
        encoding="utf-8",
    )
    return server


def _catalog_with_mcp_tool(tool_name: str) -> ExtensionCatalog:
    return publish_catalog(
        sources=[],
        tools=[
            ExtensionToolInventoryItem(
                name=tool_name,
                source_id="playwright",
                description="Open a browser page.",
                risk_level=RiskLevel.MEDIUM,
                required_capabilities={"browser:control"},
                input_schema={"type": "object"},
            )
        ],
        skills=[],
    )


def _assignment(*, allowed_tools: list[str]) -> TeamAssignment:
    root_run_id = uuid4()
    return TeamAssignment(
        root_run_id=root_run_id,
        parent_run_id=root_run_id,
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="teammate",
        runtime_route="team-role",
        goal="Inspect repository",
        allowed_tools=allowed_tools,
    )
