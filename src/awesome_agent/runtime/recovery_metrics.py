from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from uuid import UUID

from pydantic import BaseModel

from awesome_agent.domain.enums import EventType
from awesome_agent.observability.repository import (
    DurableModelCall,
    ObservabilityRepository,
)
from awesome_agent.persistence.budget import BudgetRepository
from awesome_agent.persistence.team import TeamRepository
from awesome_agent.persistence.validation import ValidationRepository
from awesome_agent.runtime.repository import RuntimeRepository


class RecoveryMetricWarning(BaseModel):
    kind: str
    message: str


class RecoveryMetricItem(BaseModel):
    key: str
    count: int


class RecoveryProviderMetric(BaseModel):
    provider: str
    model: str
    total: int
    failed: int


class VerifierRecoveryMetric(BaseModel):
    assignments_total: int
    failed_validation_reports: int
    rework_requests: int


class RecoveryBudgetMetric(BaseModel):
    total_tokens: int
    reasoning_tokens: int
    active_seconds: int
    model_call_count: int
    threshold_status: str
    budget_exhausted_events: int


class RecoveryMetricsReport(BaseModel):
    run_id: UUID
    parent_run_id: UUID | None
    root_run_id: UUID | None
    totals: dict[str, int]
    by_action: list[RecoveryMetricItem]
    by_role: list[RecoveryMetricItem]
    by_failure_kind: list[RecoveryMetricItem]
    by_provider_model: list[RecoveryProviderMetric]
    verifier: VerifierRecoveryMetric
    budgets: RecoveryBudgetMetric
    recommendations: list[str]
    warnings: list[RecoveryMetricWarning]


class RecoveryMetricsService:
    def __init__(
        self,
        *,
        runtime_repository: RuntimeRepository,
        observability_repository: ObservabilityRepository,
        budget_repository: BudgetRepository | None = None,
        validation_repository: ValidationRepository | None = None,
        team_repository: TeamRepository | None = None,
    ) -> None:
        self._runtime = runtime_repository
        self._observability = observability_repository
        self._budgets = budget_repository
        self._validation = validation_repository
        self._teams = team_repository

    async def report_for_run(self, run_id: UUID) -> RecoveryMetricsReport:
        run = await self._runtime.get_run(run_id)
        events = await self._runtime.list_events(run_id)
        model_calls = await self._observability.list_model_calls_for_run(run_id)
        warnings: list[RecoveryMetricWarning] = []
        actions = Counter[str]()
        failure_kinds = Counter[str]()

        for event in events:
            action = _action_for_event(event.event_type)
            if action is not None:
                actions[action] += 1
            failure_kind = event.payload.get("failure_kind")
            if isinstance(failure_kind, str):
                failure_kinds[failure_kind] += 1

        assignments_total = 0
        rework_requests = actions["verifier_rework"]
        role_counts = Counter[str]()
        if self._teams is None:
            warnings.append(
                RecoveryMetricWarning(
                    kind="team_repository_missing",
                    message="No team repository is configured.",
                )
            )
        else:
            root_run_id = run.root_run_id or run.id
            assignments = await self._teams.list_assignments(
                root_run_id,
                include_inactive=True,
            )
            assignments_total = len(assignments)
            role_counts.update(assignment.kind.value for assignment in assignments)
            for result in await self._teams.list_child_results(run.id):
                if result.failure_kind:
                    failure_kinds[result.failure_kind] += 1
                    mapped = _action_for_failure_kind(result.failure_kind)
                    actions[mapped] += 1

        failed_validation_reports = 0
        if self._validation is None:
            warnings.append(
                RecoveryMetricWarning(
                    kind="validation_repository_missing",
                    message="No validation repository is configured.",
                )
            )
        else:
            validation_reports = await self._validation.list_for_run(run_id)
            failed_validation_reports = sum(
                1 for item in validation_reports if item.report.status == "failed"
            )
            for item in validation_reports:
                for gate in item.gates:
                    if gate.failure_kind:
                        failure_kinds[gate.failure_kind] += 1
                        actions[_action_for_failure_kind(gate.failure_kind)] += 1

        ledger = None
        if self._budgets is None:
            warnings.append(
                RecoveryMetricWarning(
                    kind="budget_repository_missing",
                    message="No budget repository is configured.",
                )
            )
        else:
            ledger = await self._budgets.get_ledger(run_id)

        if not any("route" in call.model.lower() for call in model_calls):
            warnings.append(
                RecoveryMetricWarning(
                    kind="route_attempt_evidence_missing",
                    message=(
                        "Provider/model aggregates are based on model-call rows; "
                        "route-attempt rows are not yet separately durable."
                    ),
                )
            )

        recommendations = _recommendations(actions, failure_kinds)
        return RecoveryMetricsReport(
            run_id=run.id,
            parent_run_id=run.parent_run_id,
            root_run_id=run.root_run_id,
            totals={
                "events": len(events),
                "actions": sum(actions.values()),
                "model_calls": len(model_calls),
                "failed_model_calls": sum(
                    1 for call in model_calls if call.status == "failed"
                ),
            },
            by_action=_items(actions),
            by_role=_items(role_counts),
            by_failure_kind=_items(failure_kinds),
            by_provider_model=_provider_metrics(model_calls),
            verifier=VerifierRecoveryMetric(
                assignments_total=assignments_total,
                failed_validation_reports=failed_validation_reports,
                rework_requests=rework_requests,
            ),
            budgets=RecoveryBudgetMetric(
                total_tokens=(
                    (ledger.total_input_tokens + ledger.total_output_tokens)
                    if ledger is not None
                    else 0
                ),
                reasoning_tokens=ledger.total_reasoning_tokens if ledger else 0,
                active_seconds=ledger.active_seconds if ledger else 0,
                model_call_count=ledger.model_call_count if ledger else 0,
                threshold_status=ledger.threshold_status
                if ledger is not None
                else "within_budget",
                budget_exhausted_events=actions["budget_exhausted"],
            ),
            recommendations=recommendations,
            warnings=warnings,
        )


def _action_for_event(event_type: EventType) -> str | None:
    return {
        EventType.DISPATCH_RETRY_SCHEDULED: "provider_retry",
        EventType.TEAM_PLAN_REPAIR_CREATED: "leader_replan",
        EventType.TEAM_PLAN_REPAIR_APPLIED: "leader_replan",
        EventType.TEAM_REWORK_REQUESTED: "verifier_rework",
        EventType.TEAM_REWORK_EXHAUSTED: "verifier_rework",
        EventType.BUDGET_EXHAUSTED: "budget_exhausted",
        EventType.APPROVAL_REQUESTED: "approval_blocked",
    }.get(event_type)


def _action_for_failure_kind(failure_kind: str) -> str:
    lowered = failure_kind.lower()
    if "patch" in lowered or "conflict" in lowered:
        return "patch_conflict_recovery"
    if "validation" in lowered or "gate" in lowered:
        return "same_child_validation_rework"
    if "tool" in lowered:
        return "tool_failure_rework"
    return "unclassified_recovery"


def _provider_metrics(
    model_calls: Iterable[DurableModelCall],
) -> list[RecoveryProviderMetric]:
    totals: Counter[tuple[str, str]] = Counter()
    failures: Counter[tuple[str, str]] = Counter()
    for call in model_calls:
        provider = call.provider
        model = call.model
        key = (provider, model)
        totals[key] += 1
        if call.status == "failed":
            failures[key] += 1
    return [
        RecoveryProviderMetric(
            provider=provider,
            model=model,
            total=count,
            failed=failures[(provider, model)],
        )
        for (provider, model), count in sorted(totals.items())
    ]


def _items(counter: Counter[str]) -> list[RecoveryMetricItem]:
    return [
        RecoveryMetricItem(key=key, count=count)
        for key, count in sorted(counter.items())
        if count > 0
    ]


def _recommendations(
    actions: Counter[str],
    failure_kinds: Counter[str],
) -> list[str]:
    recommendations: list[str] = []
    if actions["verifier_rework"]:
        recommendations.append(
            "Verifier rework is present; inspect verifier feedback before "
            "changing retry or rework budgets."
        )
    if actions["budget_exhausted"]:
        recommendations.append(
            "Budget exhaustion is present; inspect token pressure before "
            "increasing recovery budgets."
        )
    if failure_kinds and actions["unclassified_recovery"]:
        recommendations.append(
            "Some recovery evidence is unclassified; add taxonomy coverage "
            "before tuning policy."
        )
    return recommendations
