import pytest

from awesome_agent.domain.enums import AgentKind, RunIntent, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.team import InMemoryTeamRepository
from awesome_agent.runtime.dispatch import ChildRunWait
from awesome_agent.runtime.graphs import TEAM_ROLE_GRAPH, TEAM_ROLE_VERSION
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamAssignmentStatus,
)
from awesome_agent.runtime.team_role_graph import TeamRoleGraph


@pytest.mark.asyncio
async def test_teammate_loads_assignment_permissions() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamRoleGraph(team_repository=teams)
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.read"],
            allowed_skills=["repository-inspection"],
        )
    )

    state, recovered = await graph.execute(run, agent, repository=runtime)

    assert not recovered
    assert state["allowed_tools"] == ["repo.read"]
    assert state["allowed_skills"] == ["repository-inspection"]


@pytest.mark.asyncio
async def test_teammate_can_create_limited_subagents() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamRoleGraph(team_repository=teams)
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            can_delegate=True,
            max_subagents=3,
            handoff_context={"subagent_goals": ["Read README", "Inspect tests"]},
        )
    )

    with pytest.raises(ChildRunWait, match="waiting_subagents"):
        await graph.execute(run, agent, repository=runtime)

    subagents = await runtime.list_child_runs(run.id)
    assignments = await teams.list_assignments(run.root_run_id or run.id)

    assert len(subagents) == 2
    assert all(child.depth == 2 for child in subagents)
    assert [
        item.kind for item in assignments if item.parent_run_id == run.id
    ] == [TeamAssignmentKind.SUBAGENT, TeamAssignmentKind.SUBAGENT]


@pytest.mark.asyncio
async def test_subagent_cannot_delegate() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamRoleGraph(team_repository=teams)
    run, agent = _role_run(kind=TeamAssignmentKind.SUBAGENT)
    await runtime.create_run(run, agent)
    await teams.create_assignment(_assignment(run, kind=TeamAssignmentKind.SUBAGENT))

    state, _ = await graph.execute(run, agent, repository=runtime)

    assert state["phase"] == "completed"
    assert await runtime.list_child_runs(run.id) == []


@pytest.mark.asyncio
async def test_teammate_resumes_after_subagents_terminal() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamRoleGraph(team_repository=teams)
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            can_delegate=True,
            max_subagents=1,
            handoff_context={"subagent_goals": ["Read README"]},
        )
    )

    with pytest.raises(ChildRunWait):
        await graph.execute(run, agent, repository=runtime)
    subagent_assignment = next(
        item
        for item in await teams.list_assignments(run.root_run_id or run.id)
        if item.kind is TeamAssignmentKind.SUBAGENT
    )
    await teams.record_child_terminal(
        subagent_assignment.child_run_id,
        status=TeamAssignmentStatus.COMPLETED,
    )

    state, recovered = await graph.execute(run, agent, repository=runtime)

    assert not recovered
    assert state["phase"] == "completed"


def _role_run(kind: TeamAssignmentKind) -> tuple[Run, Agent]:
    root_id = Run(goal="root", mode=RunMode.TEAM).id
    parent_id = root_id if kind is TeamAssignmentKind.TEAMMATE else Run(goal="p").id
    run = Run(
        goal=kind.value,
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        parent_run_id=parent_id,
        root_run_id=root_id,
        depth=(1 if kind is TeamAssignmentKind.TEAMMATE else 2),
        child_role=kind.value,
        graph_name=TEAM_ROLE_GRAPH,
        graph_version=TEAM_ROLE_VERSION,
    )
    agent = Agent(
        run_id=run.id,
        kind=(
            AgentKind.TEAMMATE
            if kind is TeamAssignmentKind.TEAMMATE
            else AgentKind.SUBAGENT
        ),
        profile=kind.value,
        model="fake",
    )
    return run, agent


def _assignment(
    run: Run,
    *,
    kind: TeamAssignmentKind,
    allowed_tools: list[str] | None = None,
    allowed_skills: list[str] | None = None,
    can_delegate: bool = False,
    max_subagents: int = 0,
    handoff_context: dict[str, object] | None = None,
) -> TeamAssignment:
    return TeamAssignment(
        root_run_id=run.root_run_id or run.id,
        parent_run_id=run.parent_run_id or run.id,
        child_run_id=run.id,
        kind=kind,
        role_profile=kind.value,
        graph_name=TEAM_ROLE_GRAPH,
        graph_version=TEAM_ROLE_VERSION,
        goal=run.goal,
        allowed_tools=allowed_tools or [],
        allowed_skills=allowed_skills or [],
        can_delegate=can_delegate,
        max_subagents=max_subagents,
        handoff_context=handoff_context or {},
    )
