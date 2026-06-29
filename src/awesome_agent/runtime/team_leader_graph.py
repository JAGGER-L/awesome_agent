from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import NotRequired, TypedDict

from pydantic import ValidationError

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.artifacts.repository import ArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import (
    AgentKind,
    DispatchStatus,
    EventType,
    RunIntent,
    RunMode,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import (
    ModelMessage,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    SystemMessage,
    TransientModelError,
    UserMessage,
)
from awesome_agent.persistence.budget import BudgetRepository
from awesome_agent.persistence.team import TeamRepository
from awesome_agent.runtime.budget import BudgetPolicy
from awesome_agent.runtime.dispatch import (
    ChildRunWait,
    PermanentExecutionError,
    TransientExecutionError,
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
    validate_assignment_graph,
)
from awesome_agent.runtime.team_budget import build_team_attribution, ensure_team_budget
from awesome_agent.runtime.team_context import compact_team_payload
from awesome_agent.runtime.team_planning import (
    TeamPlan,
    TeamPlanTeammate,
    validate_team_plan_for_intent,
)
from awesome_agent.sandbox.process import run_process

_TEAM_INLINE_PAYLOAD_TOKENS = 1200
ProviderResolver = Callable[[str], ModelProvider]
_TEAM_PLAN_MAX_ATTEMPTS = 2


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
    ) -> None:
        self.team_repository = team_repository
        self.provider_resolver = provider_resolver
        self.model_resolver = model_resolver
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

    async def _create_team_plan(
        self,
        run: Run,
        leader: Agent,
        *,
        event_sink: object | None,
    ) -> tuple[TeamPlan, int]:
        if self.provider_resolver is None:
            raise PermanentExecutionError("team_plan_provider_unavailable")
        if self.model_resolver is None:
            raise PermanentExecutionError("team_model_resolver_unavailable")
        provider = self.provider_resolver(leader.model)
        messages = _initial_team_plan_messages(run)
        last_error = ""
        for attempt in range(1, _TEAM_PLAN_MAX_ATTEMPTS + 1):
            try:
                turn = await provider.complete(
                    ModelRequest(
                        messages=messages,
                        tools=[],
                    )
                )
            except TransientModelError as error:
                raise TransientExecutionError(str(error)) from error
            except ModelProviderError as error:
                raise PermanentExecutionError(str(error)) from error
            try:
                plan = validate_team_plan_for_intent(
                    TeamPlan.model_validate_json(turn.assistant.content),
                    intent=run.intent,
                )
            except (ValidationError, ValueError) as error:
                last_error = str(error)
                await _emit_if_callable(
                    event_sink,
                    EventType.TEAM_PLAN_REJECTED,
                    {
                        "run_id": str(run.id),
                        "agent_id": str(leader.id),
                        "attempt": attempt,
                        "error": last_error[:2000],
                    },
                    f"team-plan-rejected:{attempt}",
                )
                if attempt >= _TEAM_PLAN_MAX_ATTEMPTS:
                    raise PermanentExecutionError(
                        f"team_plan_invalid: {last_error[:500]}"
                    ) from error
                messages = [
                    *messages,
                    turn.assistant,
                    UserMessage(
                        content=(
                            "Your previous TeamPlan was rejected. Fix these "
                            "validation errors and return only corrected JSON: "
                            f"{last_error[:2000]}"
                        )
                    ),
                ]
                continue
            await _emit_if_callable(
                event_sink,
                EventType.TEAM_PLAN_CREATED,
                {
                    "run_id": str(run.id),
                    "agent_id": str(leader.id),
                    "attempt": attempt,
                    "teammate_count": len(plan.teammates),
                    "rationale": plan.rationale[:2000],
                },
                "team-plan-created",
            )
            return plan, attempt
        raise PermanentExecutionError(f"team_plan_invalid: {last_error[:500]}")

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


def _initial_team_plan_messages(run: Run) -> list[ModelMessage]:
    intent_rules = (
        "The root run is read-only. Every teammate must set can_write=false and "
        "must not receive write tools."
        if run.intent is RunIntent.READ_ONLY
        else "The root run may modify files. Grant write tools only when the "
        "teammate goal truly needs file changes or shell execution."
    )
    return [
        SystemMessage(
            content=(
                "You are the Leader planning a coding-agent team. Return only "
                "valid JSON matching this schema: "
                "{"
                '"rationale":"short reason",'
                '"teammates":[{'
                '"role_profile":"lowercase-slug",'
                '"goal":"specific teammate task",'
                '"allowed_tools":["repo.status"],'
                '"deferred_tools":[],'
                '"allowed_skills":[],'
                '"can_write":false,'
                '"can_delegate":false,'
                '"max_subagents":0,'
                '"acceptance_criteria":["observable completion criterion"]'
                "}]"
                "}. Create 1 to 3 teammates. Do not create, name, describe, "
                "or direct Verifier agents. Do not include subagent_goals, "
                "delegation_guidance, or any Subagent task description. You may "
                "only set can_delegate and max_subagents for a teammate."
            )
        ),
        UserMessage(
            content=(
                f"Root goal: {run.goal}\n"
                f"Root intent: {run.intent.value}\n"
                f"{intent_rules}\n"
                "Known tools: repo.status, repo.list, repo.search, repo.read, "
                "repo.instructions, repo.diff, repo.apply_patch, shell.execute, "
                "team.create_subagent.\n"
                "Prefer the smallest useful team."
            )
        ),
    ]


async def _emit_if_callable(
    event_sink: object | None,
    event_type: EventType,
    payload: dict[str, object],
    transition_id: str,
) -> None:
    if callable(event_sink):
        await event_sink(event_type, payload, transition_id)
