import asyncio
from uuid import uuid4

from awesome_agent.domain.enums import AgentKind, AgentStatus, RiskLevel, RunStatus
from awesome_agent.domain.models import Agent, Run
from awesome_agent.extensions.catalog import publish_catalog
from awesome_agent.extensions.diagnostics import (
    ExtensionDiagnosticsService,
    diff_extension_catalogs,
)
from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionHealthSnapshot,
    ExtensionSourceSnapshot,
    ExtensionToolInventoryItem,
)
from awesome_agent.persistence.tool_invocations import (
    DurableToolInvocation,
    InMemoryToolInvocationRepository,
)
from awesome_agent.runtime.repository import InMemoryRuntimeRepository


def test_extension_catalog_diff_reports_added_tools() -> None:
    diff = diff_extension_catalogs(
        _catalog("v1", tools=[]),
        _catalog("v2", tools=["mcp.github.search"]),
    )

    assert diff.added_tools == ["mcp.github.search"]
    assert diff.removed_tools == []
    assert diff.from_version == "v1"
    assert diff.to_version == "v2"


def test_extension_diagnostics_reports_catalog_denials_and_stale_runs() -> None:
    catalog = _catalog("ext_new", tools=["mcp.github.search"])
    runtime = InMemoryRuntimeRepository()
    tools = InMemoryToolInvocationRepository()
    stale_run = Run(
        goal="Long running",
        status=RunStatus.RUNNING,
        extension_catalog_version="ext_old",
    )

    async def arrange_and_summarize() -> dict[str, object]:
        await runtime.create_run(
            stale_run,
            Agent(
                run_id=stale_run.id,
                kind=AgentKind.LEADER,
                profile="leader",
                model="test-model",
                status=AgentStatus.READY,
            ),
        )
        await tools.upsert(
            DurableToolInvocation(
                id=uuid4(),
                run_id=stale_run.id,
                agent_id=None,
                tool_name="mcp.github.search",
                tool_version="1",
                status="denied",
                idempotency_key="tool-1",
                arguments_hash="args",
                risk_level="medium",
                error="not exposed",
            )
        )
        summary = await ExtensionDiagnosticsService(
            active_catalog=catalog,
            runtime_repository=runtime,
            tool_invocation_repository=tools,
        ).summarize()
        return summary.model_dump(mode="json")

    body = asyncio.run(arrange_and_summarize())

    assert body["active_catalog_version"] == "ext_new"
    assert body["catalog"]["tools"] == 1
    assert body["denials"][0]["reason"] == "not_assigned"
    assert body["invocation_denials"][0]["tool"] == "mcp.github.search"
    assert body["warnings"][0]["kind"] == "stale_extension_catalog"
    assert body["warnings"][0]["run_id"] == str(stale_run.id)


def _catalog(version: str, *, tools: list[str]) -> ExtensionCatalog:
    source = ExtensionSourceSnapshot(
        id="github",
        type="mcp_stdio",
        trust="user",
        health=ExtensionHealthSnapshot(status="healthy"),
    )
    catalog = publish_catalog(
        sources=[source],
        tools=[
            ExtensionToolInventoryItem(
                name=tool_name,
                source_id="github",
                description="Search",
                risk_level=RiskLevel.MEDIUM,
                required_capabilities={"network:request"},
                input_schema={"type": "object"},
            )
            for tool_name in tools
        ],
        skills=[],
    )
    return catalog.model_copy(update={"version": version})
