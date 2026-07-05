from __future__ import annotations

from uuid import uuid4

import pytest

from awesome_agent.domain.enums import AgentKind, DispatchStatus, RunIntent, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.runtime.graphs import TEAM_ROLE_ROUTE, TEAM_VERIFIER_ROUTE
from awesome_agent.runtime.team_assignments import TeamAssignmentKind
from awesome_agent.runtime.team_child_runs import (
    TeamChildWorkspace,
    create_subagent_child,
    create_teammate_child,
    create_verifier_child,
)


def _root() -> tuple[Run, Agent]:
    run = Run(
        goal="root",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        runtime_route="team-coding",
        repository_id=uuid4(),
        base_commit="abc123",
        dispatch_status=DispatchStatus.QUEUED,
    )
    agent = Agent(run_id=run.id, kind=AgentKind.LEADER, profile="leader", model="m")
    return run, agent


def test_create_teammate_child_enforces_lineage_and_assignment() -> None:
    parent, leader = _root()

    bundle = create_teammate_child(
        parent=parent,
        leader=leader,
        role_profile="backend",
        goal="patch README",
        model="teammate-model",
        allowed_tools=["repo.read", "repo.apply_patch"],
        deferred_tools=[],
        promoted_tools=[],
        allowed_skills=[],
        can_write=True,
        can_delegate=False,
        max_subagents=0,
        acceptance_criteria=["README is updated."],
        handoff_context={"plan_attempt": 1},
        workspace=TeamChildWorkspace(
            workspace_path=parent.workspace_path,
            integration_branch=parent.integration_branch,
        ),
        graph_thread_id=f"run:{parent.id}:teammate:1",
    )

    assert bundle.run.parent_run_id == parent.id
    assert bundle.run.root_run_id == parent.id
    assert bundle.run.depth == 1
    assert bundle.run.runtime_route == TEAM_ROLE_ROUTE
    assert bundle.agent.parent_agent_id == leader.id
    assert bundle.assignment.kind is TeamAssignmentKind.TEAMMATE
    assert bundle.assignment.child_run_id == bundle.run.id


def test_create_subagent_child_enforces_depth_two_and_read_only_assignment() -> None:
    root, leader = _root()
    teammate = create_teammate_child(
        parent=root,
        leader=leader,
        role_profile="backend",
        goal="patch README",
        model="teammate-model",
        allowed_tools=["repo.read", "team.create_subagent"],
        deferred_tools=[],
        promoted_tools=[],
        allowed_skills=[],
        can_write=False,
        can_delegate=True,
        max_subagents=1,
        acceptance_criteria=["Read evidence."],
        handoff_context={},
        workspace=TeamChildWorkspace(
            workspace_path=root.workspace_path,
            integration_branch=root.integration_branch,
        ),
        graph_thread_id=f"run:{root.id}:teammate:1",
    )

    bundle = create_subagent_child(
        parent=teammate.run,
        parent_agent=teammate.agent,
        tool_call_id="delegate",
        goal="inspect README",
        model="teammate-model",
        allowed_tools=["repo.read"],
        allowed_skills=[],
        acceptance_criteria=["Return README evidence."],
        handoff_context={"created_by_tool_call_id": "delegate"},
    )

    assert bundle.run.parent_run_id == teammate.run.id
    assert bundle.run.root_run_id == root.id
    assert bundle.run.depth == 2
    assert bundle.assignment.kind is TeamAssignmentKind.SUBAGENT
    assert bundle.assignment.handoff_context["created_by_tool_call_id"] == "delegate"
    assert not bundle.assignment.can_write
    assert not bundle.assignment.can_delegate


def test_create_verifier_child_uses_verifier_route_and_read_only_policy() -> None:
    parent, leader = _root()

    bundle = create_verifier_child(
        parent=parent,
        leader=leader,
        index=1,
        model="verifier-model",
        workspace=TeamChildWorkspace(
            workspace_path=parent.workspace_path,
            integration_branch=parent.integration_branch,
        ),
    )

    assert bundle.run.runtime_route == TEAM_VERIFIER_ROUTE
    assert bundle.run.depth == 1
    assert bundle.agent.kind is AgentKind.VERIFIER
    assert bundle.assignment.kind is TeamAssignmentKind.VERIFIER
    assert bundle.assignment.allowed_tools == ["repo.diff"]
    assert not bundle.assignment.can_write
    assert not bundle.assignment.can_delegate


def test_subagent_cannot_create_child_run() -> None:
    root, leader = _root()
    teammate = create_teammate_child(
        parent=root,
        leader=leader,
        role_profile="backend",
        goal="patch README",
        model="teammate-model",
        allowed_tools=["repo.read", "team.create_subagent"],
        deferred_tools=[],
        promoted_tools=[],
        allowed_skills=[],
        can_write=False,
        can_delegate=True,
        max_subagents=1,
        acceptance_criteria=["Read evidence."],
        handoff_context={},
        workspace=TeamChildWorkspace(None, None),
        graph_thread_id=f"run:{root.id}:teammate:1",
    )
    subagent = create_subagent_child(
        parent=teammate.run,
        parent_agent=teammate.agent,
        tool_call_id="delegate",
        goal="inspect README",
        model="teammate-model",
        allowed_tools=["repo.read"],
        allowed_skills=[],
        acceptance_criteria=["Return README evidence."],
    )

    with pytest.raises(ValueError, match="subagent Runs cannot create child Runs"):
        create_subagent_child(
            parent=subagent.run,
            parent_agent=subagent.agent,
            tool_call_id="nested",
            goal="nested",
            model="teammate-model",
            allowed_tools=["repo.read"],
            allowed_skills=[],
            acceptance_criteria=["Return nested evidence."],
        )
