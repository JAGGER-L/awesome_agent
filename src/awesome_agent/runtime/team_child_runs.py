from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from awesome_agent.domain.enums import (
    AgentKind,
    DispatchStatus,
    RunMode,
    WorkspaceState,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.runtime.graphs import TEAM_ROLE_ROUTE, TEAM_VERIFIER_ROUTE
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    validate_assignment_graph,
    validate_child_depth,
)


@dataclass(frozen=True, slots=True)
class TeamChildWorkspace:
    workspace_path: Path | None
    integration_branch: str | None
    workspace_state: WorkspaceState | None = None


@dataclass(frozen=True, slots=True)
class TeamChildRunBundle:
    run: Run
    agent: Agent
    assignment: TeamAssignment


def create_teammate_child(
    *,
    parent: Run,
    leader: Agent,
    role_profile: str,
    goal: str,
    model: str,
    allowed_tools: list[str],
    deferred_tools: list[str],
    promoted_tools: list[str] | None = None,
    allowed_skills: list[str],
    can_write: bool,
    can_delegate: bool,
    max_subagents: int,
    acceptance_criteria: list[str],
    handoff_context: dict[str, object],
    workspace: TeamChildWorkspace,
    graph_thread_id: str,
) -> TeamChildRunBundle:
    if parent.depth >= 2:
        raise ValueError("subagent Runs cannot create child Runs")
    child = Run(
        goal=goal,
        mode=RunMode.TEAM,
        repository_id=parent.repository_id,
        base_commit=parent.base_commit,
        intent=parent.intent,
        execution_kind=parent.execution_kind,
        parent_run_id=parent.id,
        root_run_id=parent.root_run_id or parent.id,
        depth=parent.depth + 1,
        child_role=TeamAssignmentKind.TEAMMATE.value,
        runtime_route=TEAM_ROLE_ROUTE,
        extension_catalog_version=parent.extension_catalog_version,
        dispatch_status=DispatchStatus.QUEUED,
        workspace_path=workspace.workspace_path,
        integration_branch=workspace.integration_branch,
        workspace_state=workspace.workspace_state,
        graph_thread_id=graph_thread_id,
    )
    agent = Agent(
        run_id=child.id,
        parent_agent_id=leader.id,
        kind=AgentKind.TEAMMATE,
        profile=role_profile,
        model=model,
    )
    assignment = TeamAssignment(
        root_run_id=child.root_run_id or parent.id,
        parent_run_id=parent.id,
        child_run_id=child.id,
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile=role_profile,
        runtime_route=TEAM_ROLE_ROUTE,
        goal=goal,
        allowed_tools=allowed_tools,
        deferred_tools=deferred_tools,
        promoted_tools=promoted_tools or [],
        allowed_skills=allowed_skills,
        can_write=can_write,
        can_delegate=can_delegate,
        max_subagents=max_subagents,
        acceptance_criteria=acceptance_criteria,
        handoff_context=handoff_context,
    )
    validate_child_depth(parent, child)
    validate_assignment_graph(assignment)
    return TeamChildRunBundle(run=child, agent=agent, assignment=assignment)


def create_verifier_child(
    *,
    parent: Run,
    leader: Agent,
    index: int,
    model: str,
    allowed_tools: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    workspace: TeamChildWorkspace,
) -> TeamChildRunBundle:
    if parent.depth >= 2:
        raise ValueError("subagent Runs cannot create child Runs")
    child = Run(
        goal=f"Verify team result for: {parent.goal}",
        mode=RunMode.TEAM,
        repository_id=parent.repository_id,
        base_commit=parent.base_commit,
        intent=parent.intent,
        execution_kind=parent.execution_kind,
        parent_run_id=parent.id,
        root_run_id=parent.root_run_id or parent.id,
        depth=parent.depth + 1,
        child_role=TeamAssignmentKind.VERIFIER.value,
        runtime_route=TEAM_VERIFIER_ROUTE,
        extension_catalog_version=parent.extension_catalog_version,
        dispatch_status=DispatchStatus.QUEUED,
        workspace_path=workspace.workspace_path,
        integration_branch=workspace.integration_branch,
        workspace_state=workspace.workspace_state,
        graph_thread_id=f"run:{parent.id}:verifier:{index}",
    )
    agent = Agent(
        run_id=child.id,
        parent_agent_id=leader.id,
        kind=AgentKind.VERIFIER,
        profile="verifier",
        model=model,
    )
    assignment = TeamAssignment(
        root_run_id=child.root_run_id or parent.id,
        parent_run_id=parent.id,
        child_run_id=child.id,
        kind=TeamAssignmentKind.VERIFIER,
        role_profile="verifier",
        runtime_route=TEAM_VERIFIER_ROUTE,
        goal=child.goal,
        allowed_tools=allowed_tools or ["repo.diff"],
        allowed_skills=[],
        can_write=False,
        can_delegate=False,
        max_subagents=0,
        acceptance_criteria=acceptance_criteria
        or ["Verify aggregated teammate evidence."],
    )
    validate_child_depth(parent, child)
    validate_assignment_graph(assignment)
    return TeamChildRunBundle(run=child, agent=agent, assignment=assignment)


def create_subagent_child(
    *,
    parent: Run,
    parent_agent: Agent,
    tool_call_id: str,
    goal: str,
    model: str,
    allowed_tools: list[str],
    allowed_skills: list[str],
    acceptance_criteria: list[str],
    handoff_context: dict[str, object] | None = None,
) -> TeamChildRunBundle:
    if parent.depth >= 2:
        raise ValueError("subagent Runs cannot create child Runs")
    child = Run(
        goal=goal,
        mode=RunMode.TEAM,
        repository_id=parent.repository_id,
        base_commit=parent.base_commit,
        intent=parent.intent,
        execution_kind=parent.execution_kind,
        parent_run_id=parent.id,
        root_run_id=parent.root_run_id or parent.id,
        depth=parent.depth + 1,
        child_role=TeamAssignmentKind.SUBAGENT.value,
        runtime_route=TEAM_ROLE_ROUTE,
        extension_catalog_version=parent.extension_catalog_version,
        dispatch_status=DispatchStatus.QUEUED,
        workspace_path=parent.workspace_path,
        integration_branch=parent.integration_branch,
        workspace_state=parent.workspace_state,
        graph_thread_id=f"run:{parent.id}:subagent:{tool_call_id}",
    )
    agent = Agent(
        run_id=child.id,
        parent_agent_id=parent_agent.id,
        kind=AgentKind.SUBAGENT,
        profile="subagent",
        model=model,
    )
    assignment = TeamAssignment(
        root_run_id=child.root_run_id or parent.id,
        parent_run_id=parent.id,
        child_run_id=child.id,
        kind=TeamAssignmentKind.SUBAGENT,
        role_profile="subagent",
        runtime_route=TEAM_ROLE_ROUTE,
        goal=goal,
        allowed_tools=allowed_tools,
        allowed_skills=allowed_skills,
        can_write=False,
        can_delegate=False,
        max_subagents=0,
        acceptance_criteria=acceptance_criteria,
        handoff_context=handoff_context or {},
    )
    validate_child_depth(parent, child)
    validate_assignment_graph(assignment)
    return TeamChildRunBundle(run=child, agent=agent, assignment=assignment)
