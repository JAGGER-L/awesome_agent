from __future__ import annotations

from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.domain.enums import AgentKind, EventType, RunIntent, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.runtime.graphs import TEAM_CODING_GRAPH, TEAM_CODING_VERSION
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.team_graph import TeamCodingGraph


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="deepseek-v4-pro",
        teammate_model="deepseek-v4-flash",
        verifier_model="deepseek-v4-flash",
        subagent_model="deepseek-v4-flash",
    )


@pytest.mark.asyncio
async def test_team_graph_creates_durable_team_agents_with_subagent_lineage() -> None:
    repository = InMemoryRuntimeRepository()
    run = Run(
        goal="Implement backend and verify it",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        graph_name=TEAM_CODING_GRAPH,
        graph_version=TEAM_CODING_VERSION,
        graph_thread_id=f"run:{uuid4()}",
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
    assert state["phase"] == "team_activated"
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
    assert [event[0] for event in events] == [EventType.AGENT_CREATED] * 4
    created_profiles = [event[1]["profile"] for event in events]
    assert created_profiles.count("backend-engineer") == 1
    assert created_profiles.count("repo-explorer") == 2
    assert created_profiles.count("verifier") == 1
