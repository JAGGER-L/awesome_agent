from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from awesome_agent.domain.enums import (
    AgentKind,
    ApprovalStatus,
    DispatchStatus,
    RunMode,
    RunStatus,
    WorkspaceState,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.approval_contracts import (
    DurableApproval,
    InMemoryApprovalRepository,
)
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


@pytest.mark.asyncio
async def test_team_status_tree_projects_tools_approval_and_workspace_state(
    tmp_path,
) -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    approvals = InMemoryApprovalRepository()
    root = Run(
        goal="root",
        mode=RunMode.TEAM,
        status=RunStatus.RUNNING,
        workspace_path=tmp_path / "root",
        workspace_state=WorkspaceState.READY,
    )
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
        workspace_path=tmp_path / "isolated",
        workspace_state=WorkspaceState.READY,
    )
    teammate_agent = Agent(
        run_id=teammate.id,
        parent_agent_id=leader.id,
        kind=AgentKind.TEAMMATE,
        profile="backend",
        model="m",
    )
    await runtime.create_run(teammate, teammate_agent)
    assignment = TeamAssignment(
        root_run_id=root.id,
        parent_run_id=root.id,
        child_run_id=teammate.id,
        kind=TeamAssignmentKind.TEAMMATE,
        status=TeamAssignmentStatus.ACTIVE,
        role_profile="backend",
        runtime_route="team-role",
        goal="teammate",
        allowed_tools=["repo.read", "repo.apply_patch", "missing.tool"],
        can_write=False,
        acceptance_criteria=["Return result."],
    )
    await teams.create_assignment(assignment)
    approval = DurableApproval(
        id=uuid4(),
        run_id=teammate.id,
        agent_id=teammate_agent.id,
        tool_invocation_id=uuid4(),
        tool_call_id="patch-1",
        tool_name="repo.apply_patch",
        tool_version="1",
        canonical_arguments={"patch": "diff"},
        arguments_hash="hash",
        workspace_path=str(teammate.workspace_path),
        workspace_fingerprint="fingerprint",
        capabilities=["repository:write"],
        risk_level="medium",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        status=ApprovalStatus.PENDING,
    )
    await approvals.upsert(approval)

    tree = await build_team_status_tree(
        runtime=runtime,
        teams=teams,
        run_id=root.id,
        approval_repository=approvals,
    )

    teammate_node = tree.root.children[0]
    assert teammate_node.effective_tools == ["repo.read"]
    assert [item["tool"] for item in teammate_node.denied_tools] == [
        "repo.apply_patch",
        "missing.tool",
    ]
    assert teammate_node.pending_approval == {
        "approval_id": str(approval.id),
        "tool": "repo.apply_patch",
        "risk": "medium",
        "status": "pending",
    }
    assert teammate_node.waiting_reason == "waiting_approval"
    assert teammate_node.workspace_isolated is True
    assert teammate_node.workspace_summary == "isolated ready"
