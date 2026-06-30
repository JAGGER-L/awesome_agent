from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast

from awesome_agent.artifacts.repository import ArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import AgentKind, DispatchStatus, EventType, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.observability.facade import ObservabilityFacade
from awesome_agent.persistence.budget import BudgetRepository
from awesome_agent.persistence.team import TeamRepository
from awesome_agent.persistence.validation import (
    ValidationReportWithGates,
    ValidationRepository,
)
from awesome_agent.runtime.agent_loop import TeamAgentLoop
from awesome_agent.runtime.agent_loop.team_middleware import (
    TeamRoleValidationMiddleware,
    TeamRoleValidationOutcome,
    ValidationPlanResolver,
    ValidationRunner,
)
from awesome_agent.runtime.budget import BudgetPolicy
from awesome_agent.runtime.capabilities import (
    CapabilityPurpose,
    CapabilityResolver,
    EffectiveToolPolicy,
)
from awesome_agent.runtime.dispatch import ChildRunWait, PermanentExecutionError
from awesome_agent.runtime.graphs import TEAM_ROLE_ROUTE
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.runtime.role_loop import (
    ProviderResolver,
    RoleLoop,
    RoleLoopPolicy,
    RoleLoopResult,
)
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamAssignmentStatus,
    TeamChildResult,
    validate_assignment_graph,
)
from awesome_agent.runtime.team_budget import build_team_attribution, ensure_team_budget
from awesome_agent.runtime.team_context import compact_team_payload
from awesome_agent.runtime.token_accounting import (
    TokenAccountant,
    default_token_accountant,
)
from awesome_agent.runtime.validation.config import load_validation_config
from awesome_agent.runtime.validation.detection import detect_validation_plan
from awesome_agent.runtime.validation.executor import execute_validation_plan
from awesome_agent.runtime.validation.models import ValidationPlan

_TEAM_INLINE_PAYLOAD_TOKENS = 1200


class TeamRoleState(TypedDict):
    run_id: str
    agent_id: str
    runtime_route: str
    phase: str
    result_summary: str
    allowed_tools: list[str]
    allowed_skills: list[str]
    final_answer: NotRequired[str]


class TeamRoleGraph:
    def __init__(
        self,
        *,
        team_repository: TeamRepository,
        provider_resolver: ProviderResolver | None = None,
        artifact_store: LocalArtifactStore | None = None,
        artifact_repository: ArtifactMetadataRepository | None = None,
        budget_repository: BudgetRepository | None = None,
        budget_policy: BudgetPolicy | None = None,
        validation_repository: ValidationRepository | None = None,
        validation_plan_resolver: ValidationPlanResolver | None = None,
        validation_runner: ValidationRunner | None = None,
        team_loop: TeamAgentLoop | None = None,
        observability: ObservabilityFacade | None = None,
        capability_resolver: CapabilityResolver | None = None,
        token_accountant: TokenAccountant | None = None,
    ) -> None:
        self.team_repository = team_repository
        self.capability_resolver = capability_resolver or CapabilityResolver()
        self.token_accountant = token_accountant or default_token_accountant()
        self.provider_resolver = provider_resolver
        self.team_loop = team_loop or TeamAgentLoop(observability=observability)
        self.role_loop = (
            RoleLoop(provider_resolver=provider_resolver, team_loop=self.team_loop)
            if provider_resolver is not None
            else None
        )
        self.artifact_store = artifact_store
        self.artifact_repository = artifact_repository
        self.budget_repository = budget_repository
        self.budget_policy = budget_policy
        self.validation_repository = validation_repository
        self.validation_plan_resolver = (
            validation_plan_resolver or _resolve_validation_plan
        )
        self.validation_runner = validation_runner or self._run_validation
        self.validation_middleware = TeamRoleValidationMiddleware(
            validation_plan_resolver=self.validation_plan_resolver,
            validation_runner=self.validation_runner,
            validation_repository=validation_repository,
            team_loop=self.team_loop,
        )

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
        await ensure_team_budget(
            run=run,
            repository=repository,
            budget_repository=self.budget_repository,
            policy=self.budget_policy,
            now=datetime.now(UTC),
            event_sink=event_sink,
            assignment=assignment,
            agent_id=agent.id,
        )
        tool_policy = self.capability_resolver.resolve_team_assignment(
            assignment,
            purpose=CapabilityPurpose.ROLE_EXECUTION,
        )
        allowed_tools = list(tool_policy.tool_names)
        existing_subagents = [
            item
            for item in await self.team_repository.list_assignments(
                assignment.root_run_id,
                include_inactive=True,
            )
            if item.parent_run_id == run.id and item.kind is TeamAssignmentKind.SUBAGENT
        ]
        if any(
            item.status is TeamAssignmentStatus.ACTIVE for item in existing_subagents
        ):
            raise ChildRunWait("waiting_subagents")
        subagent_goals = _subagent_goals(assignment)
        if (
            assignment.kind is TeamAssignmentKind.TEAMMATE
            and assignment.can_delegate
            and subagent_goals
            and not existing_subagents
        ):
            await self._create_subagents(
                run,
                agent,
                assignment=assignment,
                goals=subagent_goals,
                parent_tool_policy=tool_policy,
                repository=repository,
                event_sink=event_sink,
            )
            raise ChildRunWait("waiting_subagents")
        subagent_results = await self.team_repository.list_child_results(run.id)
        workspace = run.workspace_path
        result, validation_outcome = await self._execute_role_with_validation_rework(
            run,
            agent,
            assignment,
            tool_policy=tool_policy,
            allowed_tools=allowed_tools,
            workspace=workspace,
            repository=repository,
            subagent_results=subagent_results,
            event_sink=event_sink,
        )
        await self._record_result_if_needed(
            run,
            agent,
            assignment,
            result=result,
            validation_outcome=validation_outcome,
        )
        if validation_outcome is not None and not validation_outcome.passed:
            raise PermanentExecutionError("team_role_validation_failed")
        return (
            TeamRoleState(
                run_id=str(run.id),
                agent_id=str(agent.id),
                runtime_route=run.runtime_route or TEAM_ROLE_ROUTE,
                phase="completed",
                result_summary=(
                    result.summary
                    if result is not None
                    else f"{assignment.kind.value} assignment completed."
                ),
                allowed_tools=allowed_tools,
                allowed_skills=assignment.allowed_skills,
                final_answer=(
                    result.final_answer
                    if result is not None
                    else f"{assignment.kind.value} assignment completed."
                ),
            ),
            False,
        )

    async def _execute_role_with_validation_rework(
        self,
        run: Run,
        agent: Agent,
        assignment: TeamAssignment,
        *,
        tool_policy: EffectiveToolPolicy,
        allowed_tools: list[str],
        workspace: Path | None,
        repository: RuntimeRepository,
        subagent_results: list[TeamChildResult],
        event_sink: object | None,
    ) -> tuple[RoleLoopResult | None, TeamRoleValidationOutcome | None]:
        validation_feedback: str | None = None
        validation_rework_count = 0
        while True:
            result = await self._execute_role_once(
                run,
                agent,
                assignment,
                tool_policy=tool_policy,
                allowed_tools=allowed_tools,
                workspace=workspace,
                repository=repository,
                subagent_results=subagent_results,
                event_sink=event_sink,
                validation_feedback=validation_feedback,
            )
            validation_outcome = await self._validate_write_result_if_needed(
                run,
                agent,
                assignment,
                result=result,
                workspace=workspace,
                event_sink=event_sink,
            )
            if validation_outcome is None or validation_outcome.passed:
                return result, validation_outcome
            plan = self._validation_plan_for_workspace(workspace)
            max_rework_attempts = plan.max_rework_attempts if plan is not None else 0
            if (
                not validation_outcome.reworkable
                or validation_rework_count >= max_rework_attempts
            ):
                return result, validation_outcome
            validation_rework_count += 1
            validation_feedback = validation_outcome.feedback

    async def _execute_role_once(
        self,
        run: Run,
        agent: Agent,
        assignment: TeamAssignment,
        *,
        tool_policy: EffectiveToolPolicy,
        allowed_tools: list[str],
        workspace: Path | None,
        repository: RuntimeRepository,
        subagent_results: list[TeamChildResult],
        event_sink: object | None,
        validation_feedback: str | None,
    ) -> RoleLoopResult | None:
        if self.role_loop is None or workspace is None:
            return None
        role_loop = self.role_loop

        async def execute_role_operation(_: object) -> RoleLoopResult:
            return await role_loop.execute(
                run=run,
                agent=agent,
                assignment=assignment,
                policy=RoleLoopPolicy(
                    allowed_tools=allowed_tools,
                    allowed_skills=assignment.allowed_skills,
                    can_write=assignment.can_write,
                    acceptance_criteria=assignment.acceptance_criteria,
                    effective_tools=tool_policy,
                ),
                workspace=workspace,
                repository=repository,
                team_repository=self.team_repository,
                subagent_results=subagent_results,
                validation_feedback=validation_feedback,
                event_sink=event_sink,  # type: ignore[arg-type]
            )

        return await self.team_loop.run_agent_operation(
            object(),
            run=run,
            agent=agent,
            messages=[],
            assignment_id=assignment.id,
            team_role=assignment.kind.value,
            agent_kind=agent.kind.value,
            metadata={
                "team_operation": "role_execute",
                "allowed_tools": allowed_tools,
            },
            handler=execute_role_operation,
        )

    async def _validate_write_result_if_needed(
        self,
        run: Run,
        agent: Agent,
        assignment: TeamAssignment,
        *,
        result: RoleLoopResult | None,
        workspace: Path | None,
        event_sink: object | None,
    ) -> TeamRoleValidationOutcome | None:
        if workspace is None:
            return None
        patch = _result_patch(assignment, result)
        if not assignment.can_write or not isinstance(patch, str) or not patch.strip():
            return None
        return await self.validation_middleware.validate_write_result(
            run,
            agent,
            assignment=assignment,
            workspace=workspace,
            event_sink=event_sink,
        )

    def _validation_plan_for_workspace(
        self,
        workspace: Path | None,
    ) -> ValidationPlan | None:
        if workspace is None:
            return None
        return self.validation_plan_resolver(workspace)

    async def _record_result_if_needed(
        self,
        run: Run,
        agent: Agent,
        assignment: TeamAssignment,
        *,
        result: RoleLoopResult | None = None,
        validation_outcome: TeamRoleValidationOutcome | None = None,
    ) -> None:
        patch_artifact_id = None
        changed_files: list[str] = []
        evidence_artifact_refs = []
        validation_failed = (
            validation_outcome is not None and not validation_outcome.passed
        )
        status: Literal["failed", "completed"] = (
            "failed" if validation_failed else "completed"
        )
        failure_kind = "validation_failed" if validation_failed else None
        summary = str(
            result.summary
            if result is not None
            else assignment.handoff_context.get("result_summary")
            or f"{assignment.kind.value} assignment completed."
        )
        patch = (
            result.patch
            if result is not None
            else assignment.handoff_context.get("patch")
        )
        if validation_failed and validation_outcome is not None:
            summary = (
                "Validation failed before publishing child result: "
                f"{validation_outcome.summary}"
            )
        if assignment.can_write and isinstance(patch, str) and patch.strip():
            changed_files = (
                result.changed_files
                if result is not None
                else [
                    item
                    for item in assignment.handoff_context.get("changed_files", [])
                    if isinstance(item, str)
                ]
            )
        if (
            not validation_failed
            and assignment.can_write
            and isinstance(patch, str)
            and patch.strip()
            and self.artifact_store is not None
            and self.artifact_repository is not None
        ):
            metadata = self.artifact_store.write(
                run_id=run.id,
                agent_id=agent.id,
                artifact_type="patch",
                filename="team-role.patch",
                content=patch.encode("utf-8"),
                mime_type="text/x-diff",
                summary=f"Patch artifact for {assignment.kind.value} assignment.",
            )
            await self.artifact_repository.record(metadata)
            patch_artifact_id = metadata.id
        compacted_summary = await compact_team_payload(
            run_id=run.id,
            agent_id=agent.id,
            runtime_route=run.runtime_route or TEAM_ROLE_ROUTE,
            payload_kind="child-result",
            payload={"summary": summary, "changed_files": changed_files},
            artifact_store=self.artifact_store,
            artifact_repository=self.artifact_repository,
            budget_repository=self.budget_repository,
            max_inline_tokens=_TEAM_INLINE_PAYLOAD_TOKENS,
            token_accountant=self.token_accountant,
        )
        if compacted_summary.compacted:
            evidence_artifact_refs.extend(compacted_summary.artifact_refs)
            summary = compacted_summary.inline_payload["summary"]
        await self.team_repository.record_child_result(
            TeamChildResult(
                assignment_id=assignment.id,
                child_run_id=run.id,
                parent_run_id=assignment.parent_run_id,
                root_run_id=assignment.root_run_id,
                status=status,
                summary=summary,
                patch_artifact_id=patch_artifact_id,
                changed_files=changed_files,
                evidence_artifact_refs=evidence_artifact_refs,
                failure_kind=failure_kind,
            )
        )

    async def _run_validation(
        self,
        plan: ValidationPlan,
        run: Run,
        agent: Agent,
    ) -> ValidationReportWithGates:
        return await execute_validation_plan(
            plan,
            run_id=run.id,
            agent_id=agent.id,
            workspace=cast(Path, run.workspace_path),
            repository=None,
        )

    async def _create_subagents(
        self,
        run: Run,
        agent: Agent,
        *,
        assignment: TeamAssignment,
        goals: list[str],
        parent_tool_policy: EffectiveToolPolicy,
        repository: RuntimeRepository,
        event_sink: object | None,
    ) -> None:
        if assignment.kind is not TeamAssignmentKind.TEAMMATE:
            raise ValueError("only teammate assignments can create subagents")
        if not assignment.can_delegate:
            raise ValueError("assignment cannot delegate")
        if len(goals) > assignment.max_subagents:
            raise ValueError("subagent request exceeds assignment limit")
        subagent_policy = self.capability_resolver.resolve_team_assignment(
            assignment,
            purpose=CapabilityPurpose.SUBAGENT_GRANT,
            requested_tools=list(parent_tool_policy.tool_names),
        )
        subagent_tools = list(subagent_policy.tool_names)
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
                runtime_route=TEAM_ROLE_ROUTE,
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
                runtime_route=TEAM_ROLE_ROUTE,
                goal=goal,
                allowed_tools=subagent_tools,
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
                        **build_team_attribution(
                            run=child,
                            assignment=child_assignment,
                            agent_id=subagent.id,
                        ),
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


def _result_patch(
    assignment: TeamAssignment,
    result: RoleLoopResult | None,
) -> object:
    if result is not None:
        return result.patch
    return assignment.handoff_context.get("patch")


def _resolve_validation_plan(workspace: Path) -> ValidationPlan | None:
    return load_validation_config(workspace) or detect_validation_plan(workspace)
