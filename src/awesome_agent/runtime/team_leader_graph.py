from __future__ import annotations

from datetime import UTC, datetime
from typing import NotRequired, TypedDict

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.artifacts.repository import ArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import (
    AgentKind,
    DispatchStatus,
    EventType,
    RunMode,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.observability.facade import ObservabilityFacade
from awesome_agent.persistence.budget import BudgetRepository
from awesome_agent.persistence.team import TeamRepository
from awesome_agent.runtime.agent_loop import TeamAgentLoop
from awesome_agent.runtime.agent_loop.team_middleware import (
    ProviderResolver,
    TeamPlanningMiddleware,
)
from awesome_agent.runtime.budget import BudgetPolicy
from awesome_agent.runtime.dispatch import (
    ChildRunWait,
    PermanentExecutionError,
)
from awesome_agent.runtime.graphs import (
    TEAM_ROLE_ROUTE,
    TEAM_VERIFIER_ROUTE,
)
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamAssignmentStatus,
    TeamChildResult,
    validate_assignment_graph,
)
from awesome_agent.runtime.team_budget import build_team_attribution, ensure_team_budget
from awesome_agent.runtime.team_context import compact_team_payload
from awesome_agent.runtime.team_patch_aggregation import (
    apply_team_patch,
    team_aggregation_diff,
)
from awesome_agent.runtime.team_planning import (
    TeamPlan,
    TeamPlanTeammate,
)
from awesome_agent.runtime.team_rework import (
    compose_rework_goal,
    decode_rework_decision,
    rework_budget_for_failure,
)
from awesome_agent.runtime.team_verification import TeamReworkRequest

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
        provider_resolver: ProviderResolver | None = None,
        model_resolver: RoleModelResolver | None = None,
        artifact_store: LocalArtifactStore | None = None,
        artifact_repository: ArtifactMetadataRepository | None = None,
        budget_repository: BudgetRepository | None = None,
        budget_policy: BudgetPolicy | None = None,
        team_loop: TeamAgentLoop | None = None,
        observability: ObservabilityFacade | None = None,
    ) -> None:
        self.team_repository = team_repository
        self.provider_resolver = provider_resolver
        self.model_resolver = model_resolver
        self.artifact_store = artifact_store
        self.artifact_repository = artifact_repository
        self.budget_repository = budget_repository
        self.budget_policy = budget_policy
        self.team_loop = team_loop or TeamAgentLoop(observability=observability)
        self.team_planning = TeamPlanningMiddleware(
            provider_resolver=provider_resolver,
            team_loop=self.team_loop,
        )

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
            plan, plan_attempt = await self._create_team_plan(
                run,
                leader,
                event_sink=event_sink,
            )
            await self._create_teammate_children_from_plan(
                run,
                leader,
                plan=plan,
                plan_attempt=plan_attempt,
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
            and assignment.status is not TeamAssignmentStatus.RETIRED
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
        if await self._handle_verifier_rework(
            run,
            leader,
            assignments=assignments,
            verifier_assignments=verifier_assignments,
            repository=repository,
            event_sink=event_sink,
        ):
            raise ChildRunWait("waiting_children")
        verifier_results = [
            result
            for result in await self.team_repository.list_child_results(run.id)
            if any(
                result.child_run_id == assignment.child_run_id
                for assignment in verifier_assignments
            )
        ]
        if any(result.status != "completed" for result in verifier_results) or any(
            assignment.status is not TeamAssignmentStatus.COMPLETED
            for assignment in verifier_assignments
        ):
            raise PermanentExecutionError("team_verification_failed")
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

    async def _create_team_plan(
        self,
        run: Run,
        leader: Agent,
        *,
        event_sink: object | None,
    ) -> tuple[TeamPlan, int]:
        if self.model_resolver is None:
            raise PermanentExecutionError("team_model_resolver_unavailable")
        return await self.team_planning.create_team_plan(
            run,
            leader,
            event_sink=event_sink,
        )

    async def _create_teammate_children_from_plan(
        self,
        run: Run,
        leader: Agent,
        *,
        plan: TeamPlan,
        plan_attempt: int,
        repository: RuntimeRepository,
        event_sink: object | None,
    ) -> None:
        for index, teammate_plan in enumerate(plan.teammates, start=1):
            await self._create_teammate_child(
                run,
                leader,
                plan=plan,
                teammate_plan=teammate_plan,
                plan_attempt=plan_attempt,
                index=index,
                repository=repository,
                event_sink=event_sink,
            )

    async def _create_teammate_child(
        self,
        run: Run,
        leader: Agent,
        *,
        plan: TeamPlan,
        teammate_plan: TeamPlanTeammate,
        plan_attempt: int,
        index: int,
        repository: RuntimeRepository,
        event_sink: object | None,
    ) -> None:
        child = Run(
            goal=teammate_plan.goal,
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
            graph_thread_id=f"run:{run.id}:teammate:{index}",
        )
        if self.model_resolver is None:
            raise PermanentExecutionError("team_model_resolver_unavailable")
        teammate_model = self.model_resolver.resolve(
            kind=AgentKind.TEAMMATE,
            profile=teammate_plan.role_profile,
        )
        teammate_agent = Agent(
            run_id=child.id,
            parent_agent_id=leader.id,
            kind=AgentKind.TEAMMATE,
            profile=teammate_plan.role_profile,
            model=teammate_model,
        )
        handoff_context = {
            "leader_plan_rationale": plan.rationale,
            "plan_attempt": plan_attempt,
            "plan_teammate_index": index,
        }
        compacted_handoff = await compact_team_payload(
            run_id=child.id,
            agent_id=teammate_agent.id,
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
            role_profile=teammate_plan.role_profile,
            runtime_route=TEAM_ROLE_ROUTE,
            goal=child.goal,
            allowed_tools=teammate_plan.allowed_tools,
            deferred_tools=teammate_plan.deferred_tools,
            allowed_skills=teammate_plan.allowed_skills,
            can_write=teammate_plan.can_write,
            can_delegate=teammate_plan.can_delegate,
            max_subagents=teammate_plan.max_subagents,
            acceptance_criteria=teammate_plan.acceptance_criteria,
            handoff_context=compacted_handoff.inline_payload,
        )
        validate_assignment_graph(assignment)
        await repository.create_run(child, teammate_agent)
        await self.team_repository.create_assignment(assignment)
        if callable(event_sink):
            await event_sink(
                EventType.TEAM_CHILD_RUN_CREATED,
                {
                    **build_team_attribution(
                        run=child,
                        assignment=assignment,
                        agent_id=teammate_agent.id,
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
                        agent_id=teammate_agent.id,
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
            await apply_team_patch(run.workspace_path, patch)
            diff = await team_aggregation_diff(run.workspace_path)
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
        if self.model_resolver is None:
            raise PermanentExecutionError("team_model_resolver_unavailable")
        verifier_index = (
            sum(
                1
                for assignment in await self.team_repository.list_assignments(
                    run.root_run_id or run.id,
                    include_inactive=True,
                )
                if assignment.parent_run_id == run.id
                and assignment.kind is TeamAssignmentKind.VERIFIER
            )
            + 1
        )
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
            graph_thread_id=f"run:{run.id}:verifier:{verifier_index}",
        )
        verifier = Agent(
            run_id=child.id,
            parent_agent_id=leader.id,
            kind=AgentKind.VERIFIER,
            profile="verifier",
            model=self.model_resolver.resolve(
                kind=AgentKind.VERIFIER,
                profile="verifier",
            ),
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

    async def _handle_verifier_rework(
        self,
        run: Run,
        leader: Agent,
        *,
        assignments: list[TeamAssignment],
        verifier_assignments: list[TeamAssignment],
        repository: RuntimeRepository,
        event_sink: object | None,
    ) -> bool:
        verifier_results = [
            result
            for result in await self.team_repository.list_child_results(run.id)
            if any(
                result.child_run_id == assignment.child_run_id
                for assignment in verifier_assignments
            )
            and result.status == "failed"
            and decode_rework_decision(result.summary) is not None
        ]
        if not verifier_results:
            return False
        verifier_result = verifier_results[-1]
        decision = decode_rework_decision(verifier_result.summary)
        if decision is None:
            return False
        created_any = False
        for request in decision.rework_requests:
            original = _find_assignment_by_child(
                assignments,
                request.target_child_run_id,
            )
            if original is None or original.kind is not TeamAssignmentKind.TEAMMATE:
                raise PermanentExecutionError("team_rework_target_not_found")
            if _replacement_exists(assignments, verifier_result.child_run_id, original):
                continue
            await self._create_rework_child(
                run,
                leader,
                original=original,
                request=request,
                verifier_result=verifier_result,
                assignments=assignments,
                repository=repository,
                event_sink=event_sink,
            )
            created_any = True
        if created_any:
            verifier_assignment = next(
                assignment
                for assignment in verifier_assignments
                if assignment.child_run_id == verifier_result.child_run_id
            )
            await self.team_repository.retire_assignment(
                verifier_assignment.id,
                reason="rework_requested",
            )
        return created_any

    async def _create_rework_child(
        self,
        run: Run,
        leader: Agent,
        *,
        original: TeamAssignment,
        request: TeamReworkRequest,
        verifier_result: TeamChildResult,
        assignments: list[TeamAssignment],
        repository: RuntimeRepository,
        event_sink: object | None,
    ) -> None:
        if self.model_resolver is None:
            raise PermanentExecutionError("team_model_resolver_unavailable")
        lineage_id = str(
            original.handoff_context.get("previous_assignment_id") or original.id
        )
        attempt = (
            sum(
                1
                for assignment in assignments
                if str(assignment.handoff_context.get("previous_assignment_id"))
                == lineage_id
            )
            + 1
        )
        budget = rework_budget_for_failure(verifier_result.failure_kind)
        if attempt > budget:
            await _emit_if_callable(
                event_sink,
                EventType.TEAM_REWORK_EXHAUSTED,
                {
                    "root_run_id": str(run.id),
                    "previous_assignment_id": lineage_id,
                    "budget": budget,
                },
                f"team-rework-exhausted:{lineage_id}",
            )
            raise PermanentExecutionError("team_rework_exhausted")
        goal = compose_rework_goal(
            original_goal=original.goal,
            feedback_summary=verifier_result.summary,
            acceptance_criteria=request.acceptance_criteria,
        )
        child = Run(
            goal=goal,
            mode=RunMode.TEAM,
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
            graph_thread_id=f"run:{run.id}:rework:{original.child_run_id}:{attempt}",
        )
        agent = Agent(
            run_id=child.id,
            parent_agent_id=leader.id,
            kind=AgentKind.TEAMMATE,
            profile=original.role_profile,
            model=self.model_resolver.resolve(
                kind=AgentKind.TEAMMATE,
                profile=original.role_profile,
            ),
        )
        assignment = TeamAssignment(
            root_run_id=child.root_run_id or run.id,
            parent_run_id=run.id,
            child_run_id=child.id,
            kind=TeamAssignmentKind.TEAMMATE,
            role_profile=original.role_profile,
            runtime_route=TEAM_ROLE_ROUTE,
            goal=goal,
            allowed_tools=original.allowed_tools,
            deferred_tools=original.deferred_tools,
            promoted_tools=original.promoted_tools,
            allowed_skills=original.allowed_skills,
            can_write=original.can_write,
            can_delegate=original.can_delegate,
            max_subagents=original.max_subagents,
            acceptance_criteria=request.acceptance_criteria,
            handoff_context={
                "previous_assignment_id": lineage_id,
                "previous_child_run_id": str(original.child_run_id),
                "verifier_child_run_id": str(verifier_result.child_run_id),
                "verifier_feedback_summary": verifier_result.summary,
                "rework_attempt": attempt,
            },
        )
        validate_assignment_graph(assignment)
        await repository.create_run(child, agent)
        await self.team_repository.create_assignment(assignment)
        await _emit_if_callable(
            event_sink,
            EventType.TEAM_REWORK_REQUESTED,
            {
                "root_run_id": str(run.id),
                "previous_assignment_id": lineage_id,
                "previous_child_run_id": str(original.child_run_id),
                "replacement_assignment_id": str(assignment.id),
                "replacement_child_run_id": str(child.id),
                "rework_attempt": attempt,
            },
            f"team-rework:{child.id}",
        )


def _find_assignment_by_child(
    assignments: list[TeamAssignment],
    child_run_id: str,
) -> TeamAssignment | None:
    return next(
        (
            assignment
            for assignment in assignments
            if str(assignment.child_run_id) == child_run_id
        ),
        None,
    )


def _replacement_exists(
    assignments: list[TeamAssignment],
    verifier_child_run_id: object,
    original: TeamAssignment,
) -> bool:
    lineage_id = str(
        original.handoff_context.get("previous_assignment_id") or original.id
    )
    return any(
        str(assignment.handoff_context.get("previous_assignment_id")) == lineage_id
        and assignment.handoff_context.get("verifier_child_run_id")
        == str(verifier_child_run_id)
        for assignment in assignments
    )


async def _emit_if_callable(
    event_sink: object | None,
    event_type: EventType,
    payload: dict[str, object],
    transition_id: str,
) -> None:
    if callable(event_sink):
        await event_sink(event_type, payload, transition_id)
