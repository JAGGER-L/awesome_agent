from __future__ import annotations

import subprocess
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.domain.enums import AgentKind, RiskLevel, RunIntent, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.extensions.models import ExtensionCatalog, ExtensionToolInventoryItem
from awesome_agent.modeling import (
    AssistantMessage,
    ModelRequest,
    ModelStreamEvent,
    ModelTurn,
    StopReason,
    StructuredModelProvider,
    ToolCall,
    TurnCompleted,
)
from awesome_agent.persistence.team import InMemoryTeamRepository
from awesome_agent.persistence.tool_invocations import InMemoryToolInvocationRepository
from awesome_agent.runtime.graphs import TEAM_ROLE_ROUTE
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.team_assignments import TeamAssignment, TeamAssignmentKind
from awesome_agent.runtime.team_role_graph import TeamRoleGraph
from awesome_agent.tools.approval import ApprovalPolicy
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.models import ToolInvocation, ToolResult, ToolSpec
from awesome_agent.tools.registry import ToolRegistry


class SequenceProvider(StructuredModelProvider):
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = deque(turns)
        self.requests: list[ModelRequest] = []

    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        self.requests.append(request)
        yield TurnCompleted(turn=self.turns.popleft())


@pytest.mark.asyncio
async def test_assigned_extension_tool_executes_through_shared_executor(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(workspace, "init")
    _git(workspace, "config", "user.email", "test@example.com")
    _git(workspace, "config", "user.name", "Test")
    _git(workspace, "add", "README.md")
    _git(workspace, "commit", "-m", "Initial")
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    invocations = InMemoryToolInvocationRepository()
    calls: list[ToolInvocation] = []
    registry = ToolRegistry()

    async def execute_extension_tool(
        invocation: ToolInvocation,
        _: object,
    ) -> ToolResult:
        calls.append(invocation)
        return ToolResult(
            invocation_id=invocation.id,
            output={"content": "ok", "path": invocation.arguments["path"]},
        )

    registry.register(
        ToolSpec(
            name="mcp.fs.read",
            description="Read through an MCP filesystem server.",
            risk_level=RiskLevel.LOW,
            required_capabilities={"repository:read"},
            sandbox_required=False,
            input_schema={"type": "object", "properties": {}},
        ),
        execute_extension_tool,
    )
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="extension-read",
                        name="mcp.fs.read",
                        arguments_json='{"path":"README.md"}',
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Extension evidence returned."),
        ]
    )
    graph = TeamRoleGraph(
        team_repository=teams,
        provider_resolver=lambda _: provider,
        tool_repository=invocations,
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        extension_catalog_resolver=lambda _version: ExtensionCatalog(
            version="team-test-catalog",
            sources=[],
            tools=[
                ExtensionToolInventoryItem(
                    name="mcp.fs.read",
                    source_id="mcp-test",
                    description="Read from fixture MCP.",
                    risk_level=RiskLevel.LOW,
                    required_capabilities={"repository:read"},
                    input_schema={"type": "object", "properties": {}},
                )
            ],
            skills=[],
        ),
    )
    root_id = uuid4()
    run = Run(
        goal="teammate",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        parent_run_id=root_id,
        root_run_id=root_id,
        depth=1,
        child_role="teammate",
        runtime_route=TEAM_ROLE_ROUTE,
        workspace_path=workspace,
        extension_catalog_version="ext_team",
    )
    agent = Agent(
        run_id=run.id,
        kind=AgentKind.TEAMMATE,
        profile="teammate",
        model="fake",
    )
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        TeamAssignment(
            root_run_id=root_id,
            parent_run_id=root_id,
            child_run_id=run.id,
            kind=TeamAssignmentKind.TEAMMATE,
            role_profile="teammate",
            runtime_route=TEAM_ROLE_ROUTE,
            goal=run.goal,
            allowed_tools=["mcp.fs.read"],
            acceptance_criteria=["Return extension evidence."],
        )
    )

    state, _ = await graph.execute(run, agent, repository=runtime)

    assert state["phase"] == "completed"
    assert calls[0].tool_name == "mcp.fs.read"
    assert calls[0].effective_tool_names == {"mcp.fs.read"}
    assert "Unknown read-only tool" not in state["result_summary"]


def _turn(
    *,
    content: str = "",
    tool_calls: list[ToolCall] | None = None,
    stop_reason: StopReason = StopReason.COMPLETED,
) -> ModelTurn:
    return ModelTurn(
        assistant=AssistantMessage(content=content, tool_calls=tool_calls or []),
        stop_reason=stop_reason,
        model="fake-model",
        provider="fake",
    )


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True)
