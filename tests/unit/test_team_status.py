from __future__ import annotations

import pytest

from awesome_agent.domain.enums import AgentKind, DispatchStatus, RunMode, RunStatus
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.team import InMemoryTeamRepository
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamAssignmentStatus,
    TeamChildResult,
)
from awesome_agent.runtime.team_status import build_team_status_tree


@pytest.mark.asyncio
async def test_team_status_tree_groups_subagent_under_teammate() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    root = Run(goal="root", mode=RunMode.TEAM, status=RunStatus.RUNNING)
    leader = Agent(run_id=root.id, kind=AgentKind.LEADER, profile="leader", model="m")
    await runtime.create_run(root, leader)
    teammate = Run(
        goal="teammate",
        mode=RunMode.TEAM,
        parent_run_id=root.id,
        root_run_id=root.id,
        depth=1,
        child_role="teammate",
        runtime_route="team-role",
        dispatch_status=DispatchStatus.WAITING,
        status=RunStatus.WAITING,
        last_release_reason="waiting_subagents",
    )
    teammate_agent = Agent(
        run_id=teammate.id,
        parent_agent_id=leader.id,
        kind=AgentKind.TEAMMATE,
        profile="backend",
        model="m",
    )
    await runtime.create_run(teammate, teammate_agent)
    teammate_assignment = TeamAssignment(
        root_run_id=root.id,
        parent_run_id=root.id,
        child_run_id=teammate.id,
        kind=TeamAssignmentKind.TEAMMATE,
        status=TeamAssignmentStatus.ACTIVE,
        role_profile="backend",
        runtime_route="team-role",
        goal="teammate",
        allowed_tools=["repo.read"],
        can_delegate=True,
        max_subagents=1,
        acceptance_criteria=["Return result."],
    )
    await teams.create_assignment(teammate_assignment)
    subagent = Run(
        goal="subagent",
        mode=RunMode.TEAM,
        parent_run_id=teammate.id,
        root_run_id=root.id,
        depth=2,
        child_role="subagent",
        runtime_route="team-role",
        dispatch_status=DispatchStatus.TERMINAL,
        status=RunStatus.COMPLETED,
    )
    subagent_agent = Agent(
        run_id=subagent.id,
        parent_agent_id=teammate_agent.id,
        kind=AgentKind.SUBAGENT,
        profile="subagent",
        model="m",
    )
    await runtime.create_run(subagent, subagent_agent)
    subagent_assignment = TeamAssignment(
        root_run_id=root.id,
        parent_run_id=teammate.id,
        child_run_id=subagent.id,
        kind=TeamAssignmentKind.SUBAGENT,
        status=TeamAssignmentStatus.COMPLETED,
        role_profile="subagent",
        runtime_route="team-role",
        goal="subagent",
        allowed_tools=["repo.read"],
        acceptance_criteria=["Return evidence."],
    )
    await teams.create_assignment(subagent_assignment)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=subagent_assignment.id,
            child_run_id=subagent.id,
            parent_run_id=teammate.id,
            root_run_id=root.id,
            status="completed",
            summary="README evidence returned.",
        )
    )

    tree = await build_team_status_tree(runtime=runtime, teams=teams, run_id=root.id)

    assert tree.root.role == "leader"
    assert tree.root.children[0].role == "teammate"
    assert tree.root.children[0].waiting_reason == "waiting_subagents"
    assert tree.root.children[0].children[0].role == "subagent"
    assert (
        tree.root.children[0].children[0].result_summary
        == "README evidence returned."
    )
    assert tree.nodes_total == 3
    assert tree.waiting_nodes == 1
