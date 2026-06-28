from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import NotRequired, TypedDict

from awesome_agent.artifacts.repository import ArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import AgentKind, DispatchStatus, EventType, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.budget import BudgetRepository
from awesome_agent.persistence.team import TeamRepository
from awesome_agent.runtime.budget import BudgetPolicy
from awesome_agent.runtime.dispatch import ChildRunWait
from awesome_agent.runtime.graphs import (
    TEAM_ROLE_ROUTE,
    TEAM_VERIFIER_ROUTE,
)
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamAssignmentStatus,
    validate_assignment_graph,
)
from awesome_agent.runtime.team_budget import build_team_attribution, ensure_team_budget
from awesome_agent.runtime.team_context import compact_team_payload
from awesome_agent.sandbox.process import run_process

_TEAM_INLINE_PAYLOAD_TOKENS = 1200


class TeamLeaderState(TypedDict):
    run_id: str
    agent_id: str
    runtime_route: str
    phase: str
    result_summary: str
    final_answer: NotRequired[str]


class TeamLeaderGraph:
    def __init__(
        self,
        *,
        team_repository: TeamRepository,
        artifact_store: LocalArtifactStore | None = None,
        artifact_repository: ArtifactMetadataRepository | None = None,
        budget_repository: BudgetRepository | None = None,
        budget_policy: BudgetPolicy | None = None,
    ) -> None:
        self.team_repository = team_repository
        self.artifact_store = artifact_store
        self.artifact_repository = artifact_repository
        self.budget_repository = budget_repository
        self.budget_policy = budget_policy

    async def execute(
        self,
        run: Run,
        leader: Agent,
        *,
        repository: RuntimeRepository,
        event_sink: object | None = None,
    ) -> tuple[TeamLeaderState, bool]:
        root_run_id = run.root_run_id or run.id
        await ensure_team_budget(
            run=run,
            repository=repository,
            budget_repository=self.budget_repository,
            policy=self.budget_policy,
            now=datetime.now(UTC),
            event_sink=event_sink,
            agent_id=leader.id,
        )
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
        await self._aggregate_child_patches(
            run,
            event_sink=event_sink,
        )
        verifier_assignments = [
            assignment
            for assignment in assignments
            if assignment.parent_run_id == run.id
            and assignment.kind is TeamAssignmentKind.VERIFIER
        ]
        if not verifier_assignments:
            await self._create_verifier_child(
                run,
                leader,
                repository=repository,
                event_sink=event_sink,
            )
            raise ChildRunWait("waiting_verifier")
        if any(
            assignment.status is TeamAssignmentStatus.ACTIVE
            for assignment in verifier_assignments
        ):
            raise ChildRunWait("waiting_verifier")
        return (
            TeamLeaderState(
                run_id=str(run.id),
                agent_id=str(leader.id),
                runtime_route=run.runtime_route or "team-coding",
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
            runtime_route=TEAM_ROLE_ROUTE,
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
        handoff_context = {
            "subagent_goals": [
                "Inspect supporting context and return focused evidence."
            ]
        }
        compacted_handoff = await compact_team_payload(
            run_id=child.id,
            agent_id=teammate.id,
            runtime_route=TEAM_ROLE_ROUTE,
            payload_kind="handoff-context",
            payload=handoff_context,
            artifact_store=self.artifact_store,
            artifact_repository=self.artifact_repository,
            budget_repository=self.budget_repository,
            max_inline_tokens=_TEAM_INLINE_PAYLOAD_TOKENS,
        )
        assignment = TeamAssignment(
            root_run_id=child.root_run_id or run.id,
            parent_run_id=run.id,
            child_run_id=child.id,
            kind=TeamAssignmentKind.TEAMMATE,
            role_profile="teammate",
            runtime_route=TEAM_ROLE_ROUTE,
            goal=child.goal,
            allowed_tools=[],
            allowed_skills=[],
            can_write=run.intent.value == "modifying",
            can_delegate=True,
            max_subagents=3,
            acceptance_criteria=["Return evidence and changed patch artifacts."],
            handoff_context=compacted_handoff.inline_payload,
        )
        validate_assignment_graph(assignment)
        await repository.create_run(child, teammate)
        await self.team_repository.create_assignment(assignment)
        if callable(event_sink):
            await event_sink(
                EventType.TEAM_CHILD_RUN_CREATED,
                {
                    **build_team_attribution(
                        run=child,
                        assignment=assignment,
                        agent_id=teammate.id,
                    ),
                    "child_run_id": str(child.id),
                    "assignment_id": str(assignment.id),
                    "kind": assignment.kind.value,
                },
                f"team-child-created:{child.id}",
            )
            await event_sink(
                EventType.TEAM_ASSIGNMENT_CREATED,
                {
                    **build_team_attribution(
                        run=child,
                        assignment=assignment,
                        agent_id=teammate.id,
                    ),
                    "assignment_id": str(assignment.id),
                    "child_run_id": str(child.id),
                    "kind": assignment.kind.value,
                },
                f"team-assignment-created:{assignment.id}",
            )

    async def _aggregate_child_patches(
        self,
        run: Run,
        *,
        event_sink: object | None,
    ) -> None:
        if run.workspace_path is None:
            return
        if self.artifact_repository is None:
            return
        results = await self.team_repository.list_child_results(run.id)
        for result in results:
            if result.status != "completed":
                continue
            if result.patch_artifact_id is None or result.patch_aggregated:
                continue
            metadata = await self.artifact_repository.get(result.patch_artifact_id)
            patch = metadata.path.read_text(encoding="utf-8")
            await _git_apply(run.workspace_path, patch)
            diff = await _git_diff(run.workspace_path)
            await self.team_repository.mark_child_result_patch_aggregated(
                result.child_run_id
            )
            if callable(event_sink):
                await event_sink(
                    EventType.TEAM_PATCH_AGGREGATED,
                    {
                        "child_run_id": str(result.child_run_id),
                        "assignment_id": str(result.assignment_id),
                        "patch_artifact_id": str(result.patch_artifact_id),
                        "changed_files": result.changed_files,
                        "diff_changed": bool(diff.strip()),
                    },
                    f"team-patch-aggregated:{result.child_run_id}",
                )

    async def _create_verifier_child(
        self,
        run: Run,
        leader: Agent,
        *,
        repository: RuntimeRepository,
        event_sink: object | None,
    ) -> None:
        child = Run(
            goal=f"Verify team result for: {run.goal}",
            mode=RunMode.TEAM,
            repository_id=run.repository_id,
            base_commit=run.base_commit,
            intent=run.intent,
            execution_kind=run.execution_kind,
            parent_run_id=run.id,
            root_run_id=run.root_run_id or run.id,
            depth=1,
            child_role=TeamAssignmentKind.VERIFIER.value,
            runtime_route=TEAM_VERIFIER_ROUTE,
            dispatch_status=DispatchStatus.QUEUED,
            workspace_path=run.workspace_path,
            integration_branch=run.integration_branch,
            workspace_state=run.workspace_state,
            graph_thread_id=f"run:{run.id}:verifier:1",
        )
        verifier = Agent(
            run_id=child.id,
            parent_agent_id=leader.id,
            kind=AgentKind.VERIFIER,
            profile="verifier",
            model=leader.model,
        )
        assignment = TeamAssignment(
            root_run_id=child.root_run_id or run.id,
            parent_run_id=run.id,
            child_run_id=child.id,
            kind=TeamAssignmentKind.VERIFIER,
            role_profile="verifier",
            runtime_route=TEAM_VERIFIER_ROUTE,
            goal=child.goal,
            allowed_tools=["repo.diff"],
            allowed_skills=[],
            can_write=False,
            can_delegate=False,
            max_subagents=0,
            acceptance_criteria=["Verify aggregated teammate evidence."],
        )
        validate_assignment_graph(assignment)
        await repository.create_run(child, verifier)
        await self.team_repository.create_assignment(assignment)
        if callable(event_sink):
            await event_sink(
                EventType.TEAM_CHILD_RUN_CREATED,
                {
                    **build_team_attribution(
                        run=child,
                        assignment=assignment,
                        agent_id=verifier.id,
                    ),
                    "child_run_id": str(child.id),
                    "assignment_id": str(assignment.id),
                    "kind": assignment.kind.value,
                },
                f"team-child-created:{child.id}",
            )
            await event_sink(
                EventType.TEAM_ASSIGNMENT_CREATED,
                {
                    **build_team_attribution(
                        run=child,
                        assignment=assignment,
                        agent_id=verifier.id,
                    ),
                    "assignment_id": str(assignment.id),
                    "child_run_id": str(child.id),
                    "kind": assignment.kind.value,
                },
                f"team-assignment-created:{assignment.id}",
            )


async def _git_apply(workspace: Path, patch: str) -> None:
    patch_file = workspace / ".awesome-agent-team.patch"
    patch_file.write_text(patch, encoding="utf-8")
    try:
        process = await run_process(
            ["git", "apply", "--whitespace=nowarn", str(patch_file.name)],
            command_label="git apply team patch",
            workspace=workspace,
            timeout_seconds=30,
        )
        if process.exit_code != 0:
            raise RuntimeError(process.stderr or process.stdout or "git apply failed")
    finally:
        patch_file.unlink(missing_ok=True)


async def _git_diff(workspace: Path) -> str:
    process = await run_process(
        ["git", "diff", "--", "."],
        command_label="git diff team aggregation",
        workspace=workspace,
        timeout_seconds=30,
    )
    if process.exit_code != 0:
        raise RuntimeError(process.stderr or process.stdout or "git diff failed")
    return process.stdout
