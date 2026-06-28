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


class TeamLeaderState(TypedDict):
    run_id: str
    agent_id: str
    graph_name: str
    graph_version: int
    phase: str
    result_summary: str
    final_answer: NotRequired[str]


class TeamLeaderGraph:
    def __init__(self, *, team_repository: TeamRepository) -> None:
        self.team_repository = team_repository

    async def execute(
        self,
        run: Run,
        leader: Agent,
        *,
        repository: RuntimeRepository,
        event_sink: object | None = None,
    ) -> tuple[TeamLeaderState, bool]:
        root_run_id = run.root_run_id or run.id
        assignments = await self.team_repository.list_assignments(
            root_run_id,
            include_inactive=True,
        )
        teammate_assignments = [
            assignment
            for assignment in assignments
            if assignment.parent_run_id == run.id
            and assignment.kind is TeamAssignmentKind.TEAMMATE
        ]
        if not teammate_assignments:
            await self._create_teammate_child(
                run,
                leader,
                repository=repository,
                event_sink=event_sink,
            )
            raise ChildRunWait("waiting_children")
        if any(
            assignment.status is TeamAssignmentStatus.ACTIVE
            for assignment in teammate_assignments
        ):
            raise ChildRunWait("waiting_children")
        return (
            TeamLeaderState(
                run_id=str(run.id),
                agent_id=str(leader.id),
                graph_name=run.graph_name or "team-coding",
                graph_version=run.graph_version or 2,
                phase="completed",
                result_summary="Distributed team child Runs completed.",
                final_answer="Distributed team child Runs completed.",
            ),
            False,
        )

    async def _create_teammate_child(
        self,
        run: Run,
        leader: Agent,
        *,
        repository: RuntimeRepository,
        event_sink: object | None,
    ) -> None:
        child = Run(
            goal=f"Teammate task for: {run.goal}",
            mode=RunMode.TEAM,
            repository_id=run.repository_id,
            base_commit=run.base_commit,
            intent=run.intent,
            execution_kind=run.execution_kind,
            parent_run_id=run.id,
            root_run_id=run.root_run_id or run.id,
            depth=1,
            child_role=TeamAssignmentKind.TEAMMATE.value,
            graph_name=TEAM_ROLE_GRAPH,
            graph_version=TEAM_ROLE_VERSION,
            dispatch_status=DispatchStatus.QUEUED,
            workspace_path=run.workspace_path,
            integration_branch=run.integration_branch,
            workspace_state=run.workspace_state,
            graph_thread_id=f"run:{run.id}:teammate:1",
        )
        teammate = Agent(
            run_id=child.id,
            parent_agent_id=leader.id,
            kind=AgentKind.TEAMMATE,
            profile="teammate",
            model=leader.model,
        )
        assignment = TeamAssignment(
            root_run_id=child.root_run_id or run.id,
            parent_run_id=run.id,
            child_run_id=child.id,
            kind=TeamAssignmentKind.TEAMMATE,
            role_profile="teammate",
            graph_name=TEAM_ROLE_GRAPH,
            graph_version=TEAM_ROLE_VERSION,
            goal=child.goal,
            allowed_tools=[],
            allowed_skills=[],
            can_write=run.intent.value == "modifying",
            can_delegate=True,
            max_subagents=3,
            acceptance_criteria=["Return evidence and changed patch artifacts."],
        )
        validate_assignment_graph(assignment)
        await repository.create_run(child, teammate)
        await self.team_repository.create_assignment(assignment)
        if callable(event_sink):
            await event_sink(
                EventType.TEAM_CHILD_RUN_CREATED,
                {
                    "child_run_id": str(child.id),
                    "assignment_id": str(assignment.id),
                    "kind": assignment.kind.value,
                },
                f"team-child-created:{child.id}",
            )
            await event_sink(
                EventType.TEAM_ASSIGNMENT_CREATED,
                {
                    "assignment_id": str(assignment.id),
                    "child_run_id": str(child.id),
                    "kind": assignment.kind.value,
                },
                f"team-assignment-created:{assignment.id}",
            )
