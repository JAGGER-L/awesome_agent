from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from awesome_agent.domain.enums import EventType, RunIntent
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import (
    ModelMessage,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    ModelTurn,
    SystemMessage,
    ToolChoice,
    ToolChoiceMode,
    ToolDefinition,
    TransientModelError,
    UserMessage,
)
from awesome_agent.persistence.validation import (
    DurableValidationGateResult,
    DurableValidationReport,
    ValidationReportWithGates,
    ValidationRepository,
)
from awesome_agent.runtime.agent_loop.modifying_middleware import (
    validation_failure_is_reworkable,
    validation_report_snapshot,
)
from awesome_agent.runtime.agent_loop.team import TeamAgentLoop
from awesome_agent.runtime.capabilities import CapabilityPurpose, CapabilityResolver
from awesome_agent.runtime.dispatch import (
    PermanentExecutionError,
    TransientExecutionError,
)
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamChildResult,
)
from awesome_agent.runtime.team_planning import (
    TeamPlan,
    validate_team_plan_for_intent,
)
from awesome_agent.runtime.team_replanning import (
    TeamPlanRepair,
    validate_team_plan_repair,
)
from awesome_agent.runtime.team_verification import TeamVerificationDecision
from awesome_agent.runtime.validation.models import ValidationPlan
from awesome_agent.tools.repository import (
    build_modifying_registry,
    model_tool_definitions,
)

ProviderResolver = Callable[[str], ModelProvider]
ValidationPlanResolver = Callable[[Path], ValidationPlan | None]
ValidationRunner = Callable[
    [ValidationPlan, Run, Agent],
    Awaitable[ValidationReportWithGates],
]
_TEAM_PLAN_MAX_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class TeamRoleValidationOutcome:
    status: Literal["passed", "failed"]
    summary: str
    report: ValidationReportWithGates
    attempt: int
    reworkable: bool = False

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def feedback(self) -> str:
        return json.dumps(
            validation_report_snapshot(self.report),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )


class TeamVerifierInvalidOutput(PermanentExecutionError):
    def __init__(self, error: str) -> None:
        self.error = error
        super().__init__("team_verifier_invalid_output")


class TeamPlanningMiddleware:
    def __init__(
        self,
        *,
        provider_resolver: ProviderResolver | None,
        team_loop: TeamAgentLoop,
    ) -> None:
        self.provider_resolver = provider_resolver
        self.team_loop = team_loop

    async def create_team_plan(
        self,
        run: Run,
        leader: Agent,
        *,
        event_sink: object | None,
    ) -> tuple[TeamPlan, int]:
        async def plan_operation(_: object) -> tuple[TeamPlan, int]:
            return await self._create_team_plan(
                run,
                leader,
                event_sink=event_sink,
            )

        return await self.team_loop.run_agent_operation(
            object(),
            run=run,
            agent=leader,
            messages=[],
            team_role="leader",
            agent_kind=leader.kind.value,
            metadata={"team_operation": "planning"},
            handler=plan_operation,
        )

    async def create_team_plan_repair(
        self,
        run: Run,
        leader: Agent,
        *,
        assignments: list[TeamAssignment],
        child_results: list[TeamChildResult],
        verifier_child_run_id: UUID,
        verifier_feedback: str,
        attempt: int,
        event_sink: object | None,
    ) -> tuple[TeamPlanRepair, int]:
        async def repair_operation(_: object) -> tuple[TeamPlanRepair, int]:
            return await self._create_team_plan_repair(
                run,
                leader,
                assignments=assignments,
                child_results=child_results,
                verifier_child_run_id=verifier_child_run_id,
                verifier_feedback=verifier_feedback,
                attempt=attempt,
                event_sink=event_sink,
            )

        return await self.team_loop.run_agent_operation(
            object(),
            run=run,
            agent=leader,
            messages=[],
            team_role="leader",
            agent_kind=leader.kind.value,
            metadata={"team_operation": "plan_repair", "attempt": attempt},
            handler=repair_operation,
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
        provider = self.provider_resolver(leader.model)
        messages = _initial_team_plan_messages(run)
        last_error = ""
        for attempt in range(1, _TEAM_PLAN_MAX_ATTEMPTS + 1):
            attempt_messages = list(messages)

            async def complete_plan_attempt(
                _: object,
                *,
                current_messages: list[ModelMessage] = attempt_messages,
            ) -> ModelTurn:
                try:
                    return await provider.complete(
                        ModelRequest(
                            messages=current_messages,
                            tools=[],
                        )
                    )
                except TransientModelError as error:
                    raise TransientExecutionError(str(error)) from error
                except ModelProviderError as error:
                    raise PermanentExecutionError(str(error)) from error

            turn = await self.team_loop.wrap_model_call(
                object(),
                run=run,
                agent=leader,
                messages=attempt_messages,
                team_role="leader",
                agent_kind=leader.kind.value,
                metadata={"team_operation": "planning", "attempt": attempt},
                handler=complete_plan_attempt,
            )
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

    async def _create_team_plan_repair(
        self,
        run: Run,
        leader: Agent,
        *,
        assignments: list[TeamAssignment],
        child_results: list[TeamChildResult],
        verifier_child_run_id: UUID,
        verifier_feedback: str,
        attempt: int,
        event_sink: object | None,
    ) -> tuple[TeamPlanRepair, int]:
        if self.provider_resolver is None:
            raise PermanentExecutionError("team_plan_repair_provider_unavailable")
        provider = self.provider_resolver(leader.model)
        messages = _initial_team_plan_repair_messages(
            run,
            assignments=assignments,
            child_results=child_results,
            verifier_child_run_id=verifier_child_run_id,
            verifier_feedback=verifier_feedback,
        )
        last_error = ""
        for model_attempt in range(1, _TEAM_PLAN_MAX_ATTEMPTS + 1):
            attempt_messages = list(messages)

            async def complete_repair_attempt(
                _: object,
                *,
                current_messages: list[ModelMessage] = attempt_messages,
            ) -> ModelTurn:
                try:
                    return await provider.complete(
                        ModelRequest(messages=current_messages, tools=[])
                    )
                except TransientModelError as error:
                    raise TransientExecutionError(str(error)) from error
                except ModelProviderError as error:
                    raise PermanentExecutionError(str(error)) from error

            turn = await self.team_loop.wrap_model_call(
                object(),
                run=run,
                agent=leader,
                messages=attempt_messages,
                team_role="leader",
                agent_kind=leader.kind.value,
                metadata={
                    "team_operation": "plan_repair",
                    "attempt": model_attempt,
                },
                handler=complete_repair_attempt,
            )
            try:
                repair = validate_team_plan_repair(
                    TeamPlanRepair.model_validate_json(turn.assistant.content),
                    intent=run.intent,
                    assignments=assignments,
                )
            except (ValidationError, ValueError) as error:
                last_error = str(error)
                await _emit_if_callable(
                    event_sink,
                    EventType.TEAM_PLAN_REPAIR_REJECTED,
                    {
                        "run_id": str(run.id),
                        "agent_id": str(leader.id),
                        "attempt": model_attempt,
                        "operation": "plan_repair",
                        "error": last_error[:2000],
                    },
                    f"team-plan-repair-rejected:{attempt}:{model_attempt}",
                )
                if model_attempt >= _TEAM_PLAN_MAX_ATTEMPTS:
                    raise PermanentExecutionError(
                        f"team_plan_repair_invalid: {last_error[:500]}"
                    ) from error
                messages = [
                    *messages,
                    turn.assistant,
                    UserMessage(
                        content=(
                            "Your previous TeamPlanRepair was rejected. Fix "
                            "these validation errors and return only corrected "
                            f"JSON: {last_error[:2000]}"
                        )
                    ),
                ]
                continue
            await _emit_if_callable(
                event_sink,
                EventType.TEAM_PLAN_REPAIR_CREATED,
                {
                    "run_id": str(run.id),
                    "agent_id": str(leader.id),
                    "attempt": attempt,
                    "model_attempt": model_attempt,
                    "action_count": len(repair.actions),
                    "rationale": repair.rationale[:2000],
                },
                f"team-plan-repair-created:{attempt}",
            )
            return repair, model_attempt
        raise PermanentExecutionError(f"team_plan_repair_invalid: {last_error[:500]}")


class TeamRoleValidationMiddleware:
    def __init__(
        self,
        *,
        validation_plan_resolver: ValidationPlanResolver,
        validation_runner: ValidationRunner,
        validation_repository: ValidationRepository | None,
        team_loop: TeamAgentLoop,
    ) -> None:
        self.validation_plan_resolver = validation_plan_resolver
        self.validation_runner = validation_runner
        self.validation_repository = validation_repository
        self.team_loop = team_loop

    async def validate_write_result(
        self,
        run: Run,
        agent: Agent,
        *,
        assignment: TeamAssignment,
        workspace: Path,
        event_sink: object | None,
    ) -> TeamRoleValidationOutcome:
        async def validation_operation(_: object) -> TeamRoleValidationOutcome:
            return await self._validate_write_result(
                run,
                agent,
                assignment=assignment,
                workspace=workspace,
                event_sink=event_sink,
            )

        return await self.team_loop.run_agent_operation(
            object(),
            run=run,
            agent=agent,
            messages=[],
            assignment_id=assignment.id,
            team_role=assignment.kind.value,
            agent_kind=agent.kind.value,
            metadata={"team_operation": "role_validation"},
            handler=validation_operation,
        )

    async def _validate_write_result(
        self,
        run: Run,
        agent: Agent,
        *,
        assignment: TeamAssignment,
        workspace: Path,
        event_sink: object | None,
    ) -> TeamRoleValidationOutcome:
        plan = self.validation_plan_resolver(workspace)
        if plan is None or not plan.gates:
            report = _missing_validation_report(run, agent)
        else:
            report = await self.validation_runner(plan, run, agent)
        if self.validation_repository is not None:
            await self.validation_repository.record_report(
                report.report,
                gates=report.gates,
            )
        await _emit_if_callable(
            event_sink,
            EventType.VERIFICATION_CREATED,
            {
                "verification_report_id": str(report.report.id),
                "agent_id": str(agent.id),
                "assignment_id": str(assignment.id),
                "status": report.report.status,
                "attempt": report.report.attempt,
                "summary": report.report.summary,
            },
            f"team-role-validation:{report.report.id}",
        )
        status: Literal["passed", "failed"] = (
            "passed" if report.report.status == "passed" else "failed"
        )
        return TeamRoleValidationOutcome(
            status=status,
            summary=report.report.summary,
            report=report,
            attempt=report.report.attempt,
            reworkable=(
                status == "failed" and validation_failure_is_reworkable(report)
            ),
        )


class TeamVerificationMiddleware:
    def __init__(
        self,
        *,
        provider_resolver: ProviderResolver | None,
        team_loop: TeamAgentLoop,
        verifier_model_output_attempts: int = 2,
    ) -> None:
        if verifier_model_output_attempts < 1:
            raise ValueError("verifier_model_output_attempts must be at least 1")
        self.provider_resolver = provider_resolver
        self.team_loop = team_loop
        self.verifier_model_output_attempts = verifier_model_output_attempts

    async def model_decision(
        self,
        run: Run,
        agent: Agent,
        *,
        assignment: TeamAssignment,
        sibling_results: list[TeamChildResult],
        event_sink: object | None,
    ) -> TeamVerificationDecision:
        async def verifier_operation(_: object) -> TeamVerificationDecision:
            return await self._model_decision(
                run,
                agent,
                assignment=assignment,
                sibling_results=sibling_results,
                event_sink=event_sink,
            )

        return await self.team_loop.run_agent_operation(
            object(),
            run=run,
            agent=agent,
            messages=[],
            assignment_id=assignment.id,
            team_role=assignment.kind.value,
            agent_kind=agent.kind.value,
            metadata={"team_operation": "verification"},
            handler=verifier_operation,
        )

    async def _model_decision(
        self,
        run: Run,
        agent: Agent,
        *,
        assignment: TeamAssignment,
        sibling_results: list[TeamChildResult],
        event_sink: object | None,
    ) -> TeamVerificationDecision:
        if self.provider_resolver is None:
            raise PermanentExecutionError("team_verifier_provider_unavailable")
        provider = self.provider_resolver(agent.model)
        messages = _initial_verifier_messages(run, assignment, sibling_results)
        last_error = "invalid verifier output"
        for attempt in range(1, self.verifier_model_output_attempts + 1):
            started = monotonic()
            attempt_messages = list(messages)

            async def complete_verifier_attempt(
                _: object,
                *,
                current_messages: list[ModelMessage] = attempt_messages,
            ) -> ModelTurn:
                return await provider.complete(
                    ModelRequest(
                        messages=current_messages,
                        tools=_verifier_tool_definitions(run, assignment),
                        tool_choice=ToolChoice(mode=ToolChoiceMode.AUTO),
                    )
                )

            turn = await self.team_loop.wrap_model_call(
                object(),
                run=run,
                agent=agent,
                messages=attempt_messages,
                assignment_id=assignment.id,
                team_role=assignment.kind.value,
                agent_kind=agent.kind.value,
                metadata={"team_operation": "verification", "attempt": attempt},
                handler=complete_verifier_attempt,
            )
            await _emit_if_callable(
                event_sink,
                EventType.MODEL_CALL_CREATED,
                {
                    "attempt": attempt,
                    "status": "completed",
                    "provider": turn.provider,
                    "model": turn.model,
                    "stop_reason": turn.stop_reason.value,
                    "input_tokens": turn.usage.input_tokens,
                    "output_tokens": turn.usage.output_tokens,
                    "reasoning_tokens": turn.usage.reasoning_tokens,
                    "latency_ms": _elapsed_ms(started),
                },
                f"model:{agent.id}:{attempt}",
            )
            try:
                if turn.assistant.tool_calls:
                    raise ValueError("verifier must return structured JSON only")
                return _parse_decision(turn.assistant.content)
            except (ValueError, ValidationError, json.JSONDecodeError) as error:
                last_error = str(error)
                messages.extend(
                    [
                        turn.assistant,
                        SystemMessage(
                            content=(
                                "Invalid verifier output. Return only valid JSON "
                                "matching the required verification schema."
                            )
                        ),
                    ]
                )
        raise TeamVerifierInvalidOutput(last_error)


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
                "team.create_subagent, team.mailbox_list, team.mailbox_send.\n"
                "Grant mailbox tools only when bounded teammate coordination or "
                "status exchange is useful. Mailbox messages do not change "
                "assignments, grant tools, create descendants, or bypass Verifier.\n"
                "Prefer the smallest useful team."
            )
        ),
    ]


def _initial_team_plan_repair_messages(
    run: Run,
    *,
    assignments: list[TeamAssignment],
    child_results: list[TeamChildResult],
    verifier_child_run_id: UUID,
    verifier_feedback: str,
) -> list[ModelMessage]:
    assignment_payload = [
        {
            "assignment_id": str(assignment.id),
            "child_run_id": str(assignment.child_run_id),
            "kind": assignment.kind.value,
            "status": assignment.status.value,
            "role_profile": assignment.role_profile,
            "goal": assignment.goal,
            "allowed_tools": assignment.allowed_tools,
            "deferred_tools": assignment.deferred_tools,
            "promoted_tools": assignment.promoted_tools,
            "allowed_skills": assignment.allowed_skills,
            "can_write": assignment.can_write,
            "can_delegate": assignment.can_delegate,
            "max_subagents": assignment.max_subagents,
            "acceptance_criteria": assignment.acceptance_criteria,
            "handoff_context": assignment.handoff_context,
        }
        for assignment in assignments
        if assignment.kind.value == "teammate"
    ]
    result_payload = [
        {
            "assignment_id": str(result.assignment_id),
            "child_run_id": str(result.child_run_id),
            "status": result.status,
            "summary": result.summary,
            "patch_artifact_id": (
                str(result.patch_artifact_id)
                if result.patch_artifact_id is not None
                else None
            ),
            "patch_aggregated": result.patch_aggregated,
            "changed_files": result.changed_files,
            "failure_kind": result.failure_kind,
        }
        for result in child_results
    ]
    payload = {
        "root_goal": run.goal,
        "root_intent": run.intent.value,
        "verifier_child_run_id": str(verifier_child_run_id),
        "verifier_feedback": verifier_feedback,
        "teammate_assignments": assignment_payload,
        "child_results": result_payload,
    }
    return [
        SystemMessage(
            content=(
                "You are the Leader repairing a coding-agent team plan after "
                "the independent Verifier requested rework. Return only valid "
                "JSON matching this schema: {"
                '"rationale":"short reason",'
                '"actions":[{'
                '"action":"replace_teammate|add_teammate",'
                '"target_child_run_id":"required only for replace_teammate",'
                '"reason":"why this repair is needed",'
                '"teammate":{'
                '"role_profile":"lowercase-slug",'
                '"goal":"specific teammate task",'
                '"allowed_tools":["repo.status"],'
                '"deferred_tools":[],'
                '"allowed_skills":[],'
                '"can_write":false,'
                '"can_delegate":false,'
                '"max_subagents":0,'
                '"acceptance_criteria":["observable completion criterion"]'
                "}}]}"
                "}. Use replace_teammate to supersede weak or mis-scoped "
                "evidence. Use add_teammate when the existing work should stay "
                "but another bounded role is needed. Do not create Verifiers or "
                "Subagents. Do not mark the team passed."
            )
        ),
        UserMessage(content=json.dumps(payload, ensure_ascii=False)),
    ]


def _initial_verifier_messages(
    run: Run,
    assignment: TeamAssignment,
    sibling_results: list[TeamChildResult],
) -> list[ModelMessage]:
    evidence = [
        {
            "child_run_id": str(result.child_run_id),
            "status": result.status,
            "summary": result.summary,
            "patch_artifact_id": (
                str(result.patch_artifact_id)
                if result.patch_artifact_id is not None
                else None
            ),
            "patch_aggregated": result.patch_aggregated,
            "changed_files": result.changed_files,
            "failure_kind": result.failure_kind,
            "evidence_artifact_refs": [
                str(artifact_id) for artifact_id in result.evidence_artifact_refs
            ],
        }
        for result in sibling_results
    ]
    payload = {
        "root_goal": run.goal,
        "verifier_goal": assignment.goal,
        "acceptance_criteria": assignment.acceptance_criteria,
        "child_results": evidence,
    }
    return [
        SystemMessage(
            content=(
                "You are the independent Verifier for a coding-agent team. "
                "Return only valid JSON with keys: decision, summary, "
                "rework_requests, failure_kind, risks. decision must be one of "
                "passed, rework_required, failed. Do not request rework from "
                "Subagents; target only sibling Teammate child_run_id values."
            )
        ),
        UserMessage(content=json.dumps(payload, ensure_ascii=False)),
    ]


def _parse_decision(content: str) -> TeamVerificationDecision:
    raw = json.loads(content)
    return TeamVerificationDecision.model_validate(raw)


def _verifier_tool_definitions(
    run: Run,
    assignment: TeamAssignment,
) -> list[ToolDefinition]:
    if run.workspace_path is None:
        return []
    policy = CapabilityResolver().resolve_team_assignment(
        assignment,
        purpose=CapabilityPurpose.VERIFIER_REVIEW,
    )
    allowed = set(policy.tool_names)
    if not allowed:
        return []
    registry = build_modifying_registry()
    return [
        definition
        for definition in model_tool_definitions(registry)
        if definition.name in allowed
    ]


async def _emit_if_callable(
    event_sink: object | None,
    event_type: EventType,
    payload: dict[str, object],
    transition_id: str,
) -> None:
    if callable(event_sink):
        await event_sink(event_type, payload, transition_id)


def _missing_validation_report(
    run: Run,
    agent: Agent,
) -> ValidationReportWithGates:
    summary = "Validation failed: no validation gates were configured or detected."
    report = DurableValidationReport(
        run_id=run.id,
        agent_id=agent.id,
        attempt=0,
        status="failed",
        summary=summary,
    )
    gate = DurableValidationGateResult(
        report_id=report.id,
        run_id=run.id,
        gate_id="validation-plan",
        name="Validation plan",
        command=["validation-plan"],
        required=True,
        status="failed",
        exit_code=1,
        failure_kind="missing_required_gate",
        stderr_summary=summary,
    )
    return ValidationReportWithGates(report=report, gates=[gate])


def _elapsed_ms(started: float) -> int:
    return int((monotonic() - started) * 1000)


__all__ = [
    "ProviderResolver",
    "TeamPlanningMiddleware",
    "TeamRoleValidationMiddleware",
    "TeamRoleValidationOutcome",
    "TeamVerificationMiddleware",
    "TeamVerifierInvalidOutput",
    "ValidationPlanResolver",
    "ValidationRunner",
]
