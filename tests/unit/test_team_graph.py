from __future__ import annotations

from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.domain.enums import AgentKind, EventType, RunIntent, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import ToolCall
from awesome_agent.runtime.graphs import TEAM_CODING_GRAPH, TEAM_CODING_VERSION
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.team_graph import AgentAssignment, TeamCodingGraph


def _git(path, *arguments: str) -> None:
    import subprocess

    result = subprocess.run(
        ["git", *arguments],
        cwd=path,
        capture_output=True,
        check=True,
        text=True,
    )
    assert result.returncode == 0


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="deepseek-v4-pro",
        teammate_model="deepseek-v4-flash",
        verifier_model="deepseek-v4-flash",
        subagent_model="deepseek-v4-flash",
    )


@pytest.mark.asyncio
async def test_team_graph_creates_durable_team_agents_with_subagent_lineage(
    tmp_path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    repository = InMemoryRuntimeRepository()
    run = Run(
        goal="Implement backend and verify it",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        graph_name=TEAM_CODING_GRAPH,
        graph_version=TEAM_CODING_VERSION,
        graph_thread_id=f"run:{uuid4()}",
        workspace_path=tmp_path,
    )
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="deepseek-v4-pro",
    )
    await repository.create_run(run, leader)
    events: list[tuple[EventType, dict[str, object], str]] = []

    async def emit(
        event_type: EventType,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    graph = TeamCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        model_resolver=_models(),
    )

    state, recovered = await graph.execute(
        run,
        leader,
        repository=repository,
        event_sink=emit,
    )

    assert not recovered
    assert state["phase"] == "role_steps_completed"
    assert state["assignments"]["backend-engineer"]["allowed_tools"] == [
        "repo.status",
        "repo.list",
        "repo.search",
        "repo.read",
        "repo.instructions",
        "repo.diff",
        "repo.apply_patch",
        "artifact.read",
    ]
    assert state["evidence"]["repo-explorer"][0]["tool"] == "repo.status"
    assert state["evidence"]["backend-subagent"][0]["tool"] == "repo.read"
    assert state["evidence"]["backend-engineer"][0]["tool"] == "repo.apply_patch"
    assert state["evidence"]["backend-engineer"][1]["tool"] == "repo.diff"
    assert "team runtime update" in (tmp_path / "README.md").read_text(encoding="utf-8")
    agents = await repository.list_agents(run.id)
    assert [agent.kind for agent in agents] == [
        AgentKind.LEADER,
        AgentKind.TEAMMATE,
        AgentKind.TEAMMATE,
        AgentKind.VERIFIER,
        AgentKind.SUBAGENT,
    ]
    backend = next(agent for agent in agents if agent.profile == "backend-engineer")
    subagent = next(agent for agent in agents if agent.kind is AgentKind.SUBAGENT)
    assert subagent.parent_agent_id == backend.id
    assert subagent.profile == "repo-explorer"
    assert all(agent.model == "deepseek-v4-flash" for agent in agents[1:])
    agent_events = [event for event in events if event[0] is EventType.AGENT_CREATED]
    tool_events = [event for event in events if event[0] is EventType.TOOL_CALL_CREATED]
    assert len(agent_events) == 4
    assert len(tool_events) == 4
    created_profiles = [event[1]["profile"] for event in agent_events]
    assert created_profiles.count("backend-engineer") == 1
    assert created_profiles.count("repo-explorer") == 2
    assert created_profiles.count("verifier") == 1
    assert {event[1]["tool"] for event in tool_events} == {
        "repo.status",
        "repo.read",
        "repo.diff",
        "repo.apply_patch",
    }


@pytest.mark.asyncio
async def test_team_graph_rejects_tools_outside_leader_assignment_scope(
    tmp_path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    run = Run(
        goal="Implement backend and verify it",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        graph_name=TEAM_CODING_GRAPH,
        graph_version=TEAM_CODING_VERSION,
        graph_thread_id=f"run:{uuid4()}",
        workspace_path=tmp_path,
    )
    agent = Agent(
        run_id=run.id,
        kind=AgentKind.TEAMMATE,
        profile="backend-engineer",
        model="deepseek-v4-flash",
    )
    graph = TeamCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        model_resolver=_models(),
    )

    result = await graph.execute_scoped_repository_tool(
        run=run,
        agent=agent,
        assignment=AgentAssignment(
            profile="backend-engineer",
            allowed_tools=["repo.diff"],
            allowed_skills=["patch-authoring"],
            can_write=True,
            can_delegate=True,
            max_subagents=1,
            acceptance_criteria=["Inspect diff only."],
        ),
        call=ToolCall(
            call_id="shell",
            name="shell.execute",
            arguments_json='{"argv":["python","-V"]}',
        ),
    )

    assert result.is_error
    assert "not allowed" in result.content
