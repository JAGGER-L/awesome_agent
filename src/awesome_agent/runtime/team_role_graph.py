from __future__ import annotations

from typing import NotRequired, TypedDict

from awesome_agent.domain.enums import AgentKind, DispatchStatus, EventType, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.team import TeamRepository
from awesome_agent.runtime.dispatch import ChildRunWait
from awesome_agent.runtime.graphs import TEAM_ROLE_GRAPH, TEAM_ROLE_VERSION
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamAssignmentStatus,
    validate_assignment_graph,
)


class TeamRoleState(TypedDict):
    run_id: str
    agent_id: str
    graph_name: str
    graph_version: int
    phase: str
    result_summary: str
    allowed_tools: list[str]
    allowed_skills: list[str]
    final_answer: NotRequired[str]


class TeamRoleGraph:
    def __init__(self, *, team_repository: TeamRepository) -> None:
        self.team_repository = team_repository

    async def execute(
        self,
        run: Run,
        agent: Agent,
        *,
        repository: RuntimeRepository,
        event_sink: object | None = None,
    ) -> tuple[TeamRoleState, bool]:
        assignment = await self.team_repository.get_assignment_for_child_run(run.id)
        validate_assignment_graph(assignment)
        subagent_goals = _subagent_goals(assignment)
        if (
            assignment.kind is TeamAssignmentKind.TEAMMATE
            and assignment.can_delegate
            and subagent_goals
        ):
            subagents = [
                item
                for item in await self.team_repository.list_assignments(
                    assignment.root_run_id,
                    include_inactive=True,
                )
                if item.parent_run_id == run.id
                and item.kind is TeamAssignmentKind.SUBAGENT
            ]
            if not subagents:
                await self._create_subagents(
                    run,
                    agent,
                    assignment=assignment,
                    goals=subagent_goals,
                    repository=repository,
                    event_sink=event_sink,
                )
                raise ChildRunWait("waiting_subagents")
            if any(item.status is TeamAssignmentStatus.ACTIVE for item in subagents):
                raise ChildRunWait("waiting_subagents")
        return (
            TeamRoleState(
                run_id=str(run.id),
                agent_id=str(agent.id),
                graph_name=run.graph_name or TEAM_ROLE_GRAPH,
                graph_version=run.graph_version or TEAM_ROLE_VERSION,
                phase="completed",
                result_summary=f"{assignment.kind.value} assignment completed.",
                allowed_tools=assignment.allowed_tools,
                allowed_skills=assignment.allowed_skills,
                final_answer=f"{assignment.kind.value} assignment completed.",
            ),
            False,
        )

    async def _create_subagents(
        self,
        run: Run,
        agent: Agent,
        *,
        assignment: TeamAssignment,
        goals: list[str],
        repository: RuntimeRepository,
        event_sink: object | None,
    ) -> None:
        if assignment.kind is not TeamAssignmentKind.TEAMMATE:
            raise ValueError("only teammate assignments can create subagents")
        if not assignment.can_delegate:
            raise ValueError("assignment cannot delegate")
        if len(goals) > assignment.max_subagents:
            raise ValueError("subagent request exceeds assignment limit")
        for index, goal in enumerate(goals, start=1):
            child = Run(
                goal=goal,
                mode=RunMode.TEAM,
                repository_id=run.repository_id,
                base_commit=run.base_commit,
                intent=run.intent,
                execution_kind=run.execution_kind,
                parent_run_id=run.id,
                root_run_id=run.root_run_id or assignment.root_run_id,
                depth=2,
                child_role=TeamAssignmentKind.SUBAGENT.value,
                graph_name=TEAM_ROLE_GRAPH,
                graph_version=TEAM_ROLE_VERSION,
                dispatch_status=DispatchStatus.QUEUED,
                workspace_path=run.workspace_path,
                integration_branch=run.integration_branch,
                workspace_state=run.workspace_state,
                graph_thread_id=f"run:{run.id}:subagent:{index}",
            )
            subagent = Agent(
                run_id=child.id,
                parent_agent_id=agent.id,
                kind=AgentKind.SUBAGENT,
                profile="subagent",
                model=agent.model,
            )
            child_assignment = TeamAssignment(
                root_run_id=assignment.root_run_id,
                parent_run_id=run.id,
                child_run_id=child.id,
                kind=TeamAssignmentKind.SUBAGENT,
                role_profile="subagent",
                graph_name=TEAM_ROLE_GRAPH,
                graph_version=TEAM_ROLE_VERSION,
                goal=goal,
                allowed_tools=assignment.allowed_tools,
                allowed_skills=assignment.allowed_skills,
                can_write=False,
                can_delegate=False,
                max_subagents=0,
                acceptance_criteria=["Return focused evidence to the teammate."],
            )
            validate_assignment_graph(child_assignment)
            await repository.create_run(child, subagent)
            await self.team_repository.create_assignment(child_assignment)
            if callable(event_sink):
                await event_sink(
                    EventType.TEAM_CHILD_RUN_CREATED,
                    {
                        "child_run_id": str(child.id),
                        "assignment_id": str(child_assignment.id),
                        "kind": child_assignment.kind.value,
                    },
                    f"team-child-created:{child.id}",
                )


def _subagent_goals(assignment: TeamAssignment) -> list[str]:
    value = assignment.handoff_context.get("subagent_goals")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]
