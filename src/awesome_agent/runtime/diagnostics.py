from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from awesome_agent.domain.models import Agent, Run, RuntimeEvent
from awesome_agent.observability.repository import (
    DurableMetric,
    DurableModelCall,
    DurableSpan,
    ObservabilityRepository,
)
from awesome_agent.persistence.budget import BudgetRepository
from awesome_agent.persistence.team import TeamRepository
from awesome_agent.persistence.tool_invocations import ToolInvocationRepository
from awesome_agent.persistence.validation import ValidationRepository
from awesome_agent.runtime.repository import RuntimeRepository


class DiagnosticWarning(BaseModel):
    kind: str
    message: str


class RunStatusDiagnostic(BaseModel):
    status: str
    mode: str
    intent: str
    execution_kind: str
    runtime_route: str | None
    parent_run_id: UUID | None
    root_run_id: UUID | None
    depth: int
    child_role: str | None
    result_available: bool
    created_at: datetime
    updated_at: datetime


class DispatchDiagnostic(BaseModel):
    status: str
    available_at: datetime
    worker_id: UUID | None
    worker_name: str | None
    fencing_token: int
    attempt: int
    lease_acquired_at: datetime | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    last_release_reason: str | None
    last_error_present: bool


class RunTimelineEntry(BaseModel):
    sequence: int
    event_type: str
    transition_id: str | None
    agent_id: UUID | None
    task_id: UUID | None
    payload_keys: list[str]
    created_at: datetime


class AgentDiagnostic(BaseModel):
    id: UUID
    kind: str
    profile: str
    model: str
    status: str
    revision: int
    created_at: datetime
    updated_at: datetime


class AgentDiagnosticSummary(BaseModel):
    total: int
    by_status: dict[str, int]
    agents: list[AgentDiagnostic]


class BudgetDiagnosticSummary(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    reasoning_tokens: int
    active_seconds: int
    model_call_count: int
    threshold_status: str


class ModelCallDiagnostic(BaseModel):
    id: UUID
    agent_id: UUID | None
    turn: int
    provider: str
    model: str
    status: str
    stop_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    latency_ms: int | None
    error_present: bool
    created_at: datetime


class ModelDiagnosticSummary(BaseModel):
    total: int
    completed: int
    failed: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    calls: list[ModelCallDiagnostic]


class ToolInvocationDiagnostic(BaseModel):
    id: UUID
    agent_id: UUID | None
    tool_name: str
    tool_version: str
    status: str
    risk_level: str
    arguments_hash: str
    path_refs: list[str]
    artifact_refs: list[str]
    result_summary: str | None
    result_is_error: bool
    error_present: bool
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class ToolDiagnosticSummary(BaseModel):
    total: int
    by_status: dict[str, int]
    tools: list[ToolInvocationDiagnostic]


class ValidationGateDiagnostic(BaseModel):
    id: UUID
    report_id: UUID
    gate_id: str
    name: str
    required: bool
    status: str
    exit_code: int | None
    duration_ms: int | None
    failure_kind: str | None
    artifact_refs: list[str]
    created_at: datetime


class ValidationReportDiagnostic(BaseModel):
    id: UUID
    agent_id: UUID | None
    attempt: int
    status: str
    created_at: datetime
    gates: list[ValidationGateDiagnostic]


class ValidationDiagnosticSummary(BaseModel):
    reports_total: int
    failed_reports: int
    gates_total: int
    failed_gates: int
    reports: list[ValidationReportDiagnostic]


class TeamAssignmentDiagnostic(BaseModel):
    id: UUID
    parent_run_id: UUID
    child_run_id: UUID
    kind: str
    status: str
    role_profile: str
    runtime_route: str
    allowed_tools: list[str]
    can_write: bool
    can_delegate: bool
    max_subagents: int
    created_at: datetime


class TeamChildRunDiagnostic(BaseModel):
    id: UUID
    status: str
    dispatch_status: str
    child_role: str | None
    runtime_route: str | None
    depth: int
    created_at: datetime
    updated_at: datetime


class TeamChildResultDiagnostic(BaseModel):
    assignment_id: UUID
    child_run_id: UUID
    status: str
    failure_kind: str | None
    patch_aggregated: bool
    changed_files: list[str]
    evidence_artifact_refs: list[UUID]
    created_at: datetime


class TeamDiagnosticSummary(BaseModel):
    assignments_total: int
    child_runs_total: int
    child_results_total: int
    mailbox_messages_total: int
    assignments: list[TeamAssignmentDiagnostic]
    child_runs: list[TeamChildRunDiagnostic]
    child_results: list[TeamChildResultDiagnostic]


class ObservabilityDiagnosticSummary(BaseModel):
    spans_total: int
    failed_spans: int
    metrics_total: int
    model_calls_total: int
    latest_span_name: str | None
    latest_metric_name: str | None


class RelatedDiagnosticLinks(BaseModel):
    recovery_metrics: str


class RunDiagnosticSummary(BaseModel):
    run_id: UUID
    related: RelatedDiagnosticLinks
    status: RunStatusDiagnostic
    dispatch: DispatchDiagnostic
    timeline: list[RunTimelineEntry]
    agents: AgentDiagnosticSummary
    budgets: BudgetDiagnosticSummary
    models: ModelDiagnosticSummary
    tools: ToolDiagnosticSummary
    validation: ValidationDiagnosticSummary
    team: TeamDiagnosticSummary | None
    observability: ObservabilityDiagnosticSummary
    warnings: list[DiagnosticWarning]


class _MissingRepository(Protocol):
    pass


class RunDiagnosticsService:
    def __init__(
        self,
        *,
        runtime_repository: RuntimeRepository,
        observability_repository: ObservabilityRepository,
        budget_repository: BudgetRepository | None = None,
        tool_invocation_repository: ToolInvocationRepository | None = None,
        validation_repository: ValidationRepository | None = None,
        team_repository: TeamRepository | None = None,
    ) -> None:
        self._runtime = runtime_repository
        self._observability = observability_repository
        self._budgets = budget_repository
        self._tools = tool_invocation_repository
        self._validation = validation_repository
        self._teams = team_repository

    async def summarize(self, run_id: UUID) -> RunDiagnosticSummary:
        run = await self._runtime.get_run(run_id)
        events = await self._runtime.list_events(run_id)
        agents = await self._runtime.list_agents(run_id)
        spans = await self._observability.list_spans_for_run(run_id)
        metrics = await self._observability.list_metrics_for_run(run_id)
        model_calls = await self._observability.list_model_calls_for_run(run_id)
        warnings: list[DiagnosticWarning] = []

        return RunDiagnosticSummary(
            run_id=run.id,
            related=RelatedDiagnosticLinks(
                recovery_metrics=f"/runs/{run.id}/recovery-metrics",
            ),
            status=_status(run),
            dispatch=_dispatch(run),
            timeline=[_timeline_entry(event) for event in events],
            agents=_agents(agents),
            budgets=await self._budget(run_id, warnings),
            models=_models(model_calls),
            tools=await self._tool_summary(run_id, warnings),
            validation=await self._validation_summary(run_id, warnings),
            team=await self._team_summary(run, warnings),
            observability=_observability_summary(spans, metrics, model_calls),
            warnings=warnings,
        )

    async def _budget(
        self,
        run_id: UUID,
        warnings: list[DiagnosticWarning],
    ) -> BudgetDiagnosticSummary:
        if self._budgets is None:
            warnings.append(
                DiagnosticWarning(
                    kind="budget_repository_missing",
                    message="No budget repository is configured.",
                )
            )
            return BudgetDiagnosticSummary(
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                reasoning_tokens=0,
                active_seconds=0,
                model_call_count=0,
                threshold_status="within_budget",
            )
        ledger = await self._budgets.get_ledger(run_id)
        return BudgetDiagnosticSummary(
            input_tokens=ledger.total_input_tokens,
            output_tokens=ledger.total_output_tokens,
            total_tokens=ledger.total_input_tokens + ledger.total_output_tokens,
            reasoning_tokens=ledger.total_reasoning_tokens,
            active_seconds=ledger.active_seconds,
            model_call_count=ledger.model_call_count,
            threshold_status=ledger.threshold_status,
        )

    async def _tool_summary(
        self,
        run_id: UUID,
        warnings: list[DiagnosticWarning],
    ) -> ToolDiagnosticSummary:
        if self._tools is None:
            warnings.append(
                DiagnosticWarning(
                    kind="tool_repository_missing",
                    message="No tool invocation repository is configured.",
                )
            )
            return ToolDiagnosticSummary(total=0, by_status={}, tools=[])
        invocations = await self._tools.list_for_run(run_id)
        return ToolDiagnosticSummary(
            total=len(invocations),
            by_status=_counts(invocation.status for invocation in invocations),
            tools=[
                ToolInvocationDiagnostic(
                    id=invocation.id,
                    agent_id=invocation.agent_id,
                    tool_name=invocation.tool_name,
                    tool_version=invocation.tool_version,
                    status=invocation.status,
                    risk_level=invocation.risk_level,
                    arguments_hash=invocation.arguments_hash,
                    path_refs=invocation.path_refs,
                    artifact_refs=invocation.artifact_refs,
                    result_summary=_safe_text(invocation.result_summary),
                    result_is_error=invocation.result_is_error,
                    error_present=invocation.error is not None,
                    started_at=invocation.started_at,
                    completed_at=invocation.completed_at,
                    created_at=invocation.created_at,
                )
                for invocation in invocations
            ],
        )

    async def _validation_summary(
        self,
        run_id: UUID,
        warnings: list[DiagnosticWarning],
    ) -> ValidationDiagnosticSummary:
        if self._validation is None:
            warnings.append(
                DiagnosticWarning(
                    kind="validation_repository_missing",
                    message="No validation repository is configured.",
                )
            )
            return ValidationDiagnosticSummary(
                reports_total=0,
                failed_reports=0,
                gates_total=0,
                failed_gates=0,
                reports=[],
            )
        reports = await self._validation.list_for_run(run_id)
        report_items: list[ValidationReportDiagnostic] = []
        gates_total = 0
        failed_gates = 0
        for item in reports:
            gates_total += len(item.gates)
            failed_gates += sum(1 for gate in item.gates if gate.status == "failed")
            report_items.append(
                ValidationReportDiagnostic(
                    id=item.report.id,
                    agent_id=item.report.agent_id,
                    attempt=item.report.attempt,
                    status=item.report.status,
                    created_at=item.report.created_at,
                    gates=[
                        ValidationGateDiagnostic(
                            id=gate.id,
                            report_id=gate.report_id,
                            gate_id=gate.gate_id,
                            name=gate.name,
                            required=gate.required,
                            status=gate.status,
                            exit_code=gate.exit_code,
                            duration_ms=gate.duration_ms,
                            failure_kind=gate.failure_kind,
                            artifact_refs=gate.artifact_refs,
                            created_at=gate.created_at,
                        )
                        for gate in item.gates
                    ],
                )
            )
        return ValidationDiagnosticSummary(
            reports_total=len(reports),
            failed_reports=sum(1 for item in reports if item.report.status == "failed"),
            gates_total=gates_total,
            failed_gates=failed_gates,
            reports=report_items,
        )

    async def _team_summary(
        self,
        run: Run,
        warnings: list[DiagnosticWarning],
    ) -> TeamDiagnosticSummary | None:
        child_runs = await self._runtime.list_child_runs(run.id)
        if self._teams is None:
            if child_runs:
                warnings.append(
                    DiagnosticWarning(
                        kind="team_repository_missing",
                        message=(
                            "Child runs exist but no team repository is configured."
                        ),
                    )
                )
            return None
        root_run_id = run.root_run_id or run.id
        assignments = await self._teams.list_assignments(
            root_run_id,
            include_inactive=True,
        )
        child_results = await self._teams.list_child_results(run.id)
        mailbox_messages = await self._teams.list_mailbox_messages(run.id)
        if (
            not assignments
            and not child_runs
            and not child_results
            and not mailbox_messages
        ):
            return None
        return TeamDiagnosticSummary(
            assignments_total=len(assignments),
            child_runs_total=len(child_runs),
            child_results_total=len(child_results),
            mailbox_messages_total=len(mailbox_messages),
            assignments=[
                TeamAssignmentDiagnostic(
                    id=assignment.id,
                    parent_run_id=assignment.parent_run_id,
                    child_run_id=assignment.child_run_id,
                    kind=assignment.kind.value,
                    status=assignment.status.value,
                    role_profile=assignment.role_profile,
                    runtime_route=assignment.runtime_route,
                    allowed_tools=assignment.allowed_tools,
                    can_write=assignment.can_write,
                    can_delegate=assignment.can_delegate,
                    max_subagents=assignment.max_subagents,
                    created_at=assignment.created_at,
                )
                for assignment in assignments
            ],
            child_runs=[
                TeamChildRunDiagnostic(
                    id=child.id,
                    status=child.status.value,
                    dispatch_status=child.dispatch_status.value,
                    child_role=child.child_role,
                    runtime_route=child.runtime_route,
                    depth=child.depth,
                    created_at=child.created_at,
                    updated_at=child.updated_at,
                )
                for child in child_runs
            ],
            child_results=[
                TeamChildResultDiagnostic(
                    assignment_id=result.assignment_id,
                    child_run_id=result.child_run_id,
                    status=result.status,
                    failure_kind=result.failure_kind,
                    patch_aggregated=result.patch_aggregated,
                    changed_files=result.changed_files,
                    evidence_artifact_refs=result.evidence_artifact_refs,
                    created_at=result.created_at,
                )
                for result in child_results
            ],
        )


def _status(run: Run) -> RunStatusDiagnostic:
    return RunStatusDiagnostic(
        status=run.status.value,
        mode=run.mode.value,
        intent=run.intent.value,
        execution_kind=run.execution_kind.value,
        runtime_route=run.runtime_route,
        parent_run_id=run.parent_run_id,
        root_run_id=run.root_run_id,
        depth=run.depth,
        child_role=run.child_role,
        result_available=bool(run.result_text),
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _dispatch(run: Run) -> DispatchDiagnostic:
    return DispatchDiagnostic(
        status=run.dispatch_status.value,
        available_at=run.available_at,
        worker_id=run.current_worker_id,
        worker_name=run.current_worker_name,
        fencing_token=run.fencing_token,
        attempt=run.attempt,
        lease_acquired_at=run.lease_acquired_at,
        lease_expires_at=run.lease_expires_at,
        heartbeat_at=run.heartbeat_at,
        last_release_reason=_safe_text(run.last_release_reason),
        last_error_present=run.last_dispatch_error is not None,
    )


def _timeline_entry(event: RuntimeEvent) -> RunTimelineEntry:
    return RunTimelineEntry(
        sequence=event.sequence,
        event_type=event.event_type.value,
        transition_id=event.transition_id,
        agent_id=event.agent_id,
        task_id=event.task_id,
        payload_keys=sorted(str(key) for key in event.payload),
        created_at=event.created_at,
    )


def _agents(agents: list[Agent]) -> AgentDiagnosticSummary:
    return AgentDiagnosticSummary(
        total=len(agents),
        by_status=_counts(agent.status.value for agent in agents),
        agents=[
            AgentDiagnostic(
                id=agent.id,
                kind=agent.kind.value,
                profile=agent.profile,
                model=agent.model,
                status=agent.status.value,
                revision=agent.revision,
                created_at=agent.created_at,
                updated_at=agent.updated_at,
            )
            for agent in agents
        ],
    )


def _models(calls: list[DurableModelCall]) -> ModelDiagnosticSummary:
    return ModelDiagnosticSummary(
        total=len(calls),
        completed=sum(1 for call in calls if call.status == "completed"),
        failed=sum(1 for call in calls if call.status == "failed"),
        input_tokens=sum(call.input_tokens or 0 for call in calls),
        output_tokens=sum(call.output_tokens or 0 for call in calls),
        reasoning_tokens=sum(call.reasoning_tokens or 0 for call in calls),
        calls=[
            ModelCallDiagnostic(
                id=call.id,
                agent_id=call.agent_id,
                turn=call.turn,
                provider=call.provider,
                model=call.model,
                status=call.status,
                stop_reason=call.stop_reason,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                reasoning_tokens=call.reasoning_tokens,
                latency_ms=call.latency_ms,
                error_present=call.error is not None,
                created_at=call.created_at,
            )
            for call in calls
        ],
    )


def _observability_summary(
    spans: list[DurableSpan],
    metrics: list[DurableMetric],
    model_calls: list[DurableModelCall],
) -> ObservabilityDiagnosticSummary:
    return ObservabilityDiagnosticSummary(
        spans_total=len(spans),
        failed_spans=sum(1 for span in spans if span.status == "failed"),
        metrics_total=len(metrics),
        model_calls_total=len(model_calls),
        latest_span_name=spans[-1].name if spans else None,
        latest_metric_name=metrics[-1].name if metrics else None,
    )


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(Counter(str(value) for value in values))


def _safe_text(value: str | None) -> str | None:
    if value is None:
        return None
    redacted = value
    for marker in ("secret", "token", "password", "credential", "api_key"):
        redacted = redacted.replace(marker, "[redacted]")
        redacted = redacted.replace(marker.upper(), "[redacted]")
        redacted = redacted.replace(marker.title(), "[redacted]")
    return redacted[:500]
