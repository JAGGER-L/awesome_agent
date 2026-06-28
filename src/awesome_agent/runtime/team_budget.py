from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from awesome_agent.domain.enums import EventType
from awesome_agent.domain.models import Run
from awesome_agent.persistence.budget import BudgetRepository
from awesome_agent.runtime.budget import BudgetDecision, BudgetLedger, BudgetPolicy
from awesome_agent.runtime.budget import evaluate_budget as evaluate_single_budget
from awesome_agent.runtime.dispatch import PermanentExecutionError
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.runtime.team_assignments import TeamAssignment


@dataclass(frozen=True, slots=True)
class TeamBudgetSnapshot:
    run_ids: list[UUID]
    total_input_tokens: int
    total_output_tokens: int
    total_reasoning_tokens: int
    active_seconds: int
    model_call_count: int
    threshold_status: str

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def to_ledger(self) -> BudgetLedger:
        return BudgetLedger(
            total_input_tokens=self.total_input_tokens,
            total_output_tokens=self.total_output_tokens,
            total_reasoning_tokens=self.total_reasoning_tokens,
            active_seconds=self.active_seconds,
            model_call_count=self.model_call_count,
            threshold_status=self.threshold_status,
        )

    def to_event_payload(self) -> dict[str, object]:
        return {
            "team_run_ids": [str(run_id) for run_id in self.run_ids],
            "team_total_input_tokens": self.total_input_tokens,
            "team_total_output_tokens": self.total_output_tokens,
            "team_total_reasoning_tokens": self.total_reasoning_tokens,
            "team_active_seconds": self.active_seconds,
            "team_model_call_count": self.model_call_count,
            "team_threshold_status": self.threshold_status,
        }


async def load_team_budget_snapshot(
    *,
    root_run_id: UUID,
    repository: RuntimeRepository,
    budget_repository: BudgetRepository,
    now: datetime,
) -> TeamBudgetSnapshot:
    root = await repository.get_run(root_run_id)
    descendants = await repository.list_descendant_runs(root_run_id)
    run_ids = [root.id, *(run.id for run in descendants)]
    ledgers = [await budget_repository.get_ledger(run_id) for run_id in run_ids]
    active_seconds = sum(
        BudgetLedger(
            active_seconds=ledger.active_seconds,
            active_window_started_at=ledger.active_window_started_at,
        ).active_seconds_at(now)
        for ledger in ledgers
    )
    threshold_status = next(
        (
            ledger.threshold_status
            for ledger in ledgers
            if ledger.threshold_status != BudgetDecision.WITHIN_BUDGET.value
        ),
        BudgetDecision.WITHIN_BUDGET.value,
    )
    return TeamBudgetSnapshot(
        run_ids=run_ids,
        total_input_tokens=sum(ledger.total_input_tokens for ledger in ledgers),
        total_output_tokens=sum(ledger.total_output_tokens for ledger in ledgers),
        total_reasoning_tokens=sum(ledger.total_reasoning_tokens for ledger in ledgers),
        active_seconds=active_seconds,
        model_call_count=sum(ledger.model_call_count for ledger in ledgers),
        threshold_status=threshold_status,
    )


async def evaluate_team_budget(
    *,
    root_run_id: UUID,
    repository: RuntimeRepository,
    budget_repository: BudgetRepository,
    policy: BudgetPolicy,
    estimated_prompt_tokens: int,
    now: datetime,
) -> tuple[BudgetDecision, TeamBudgetSnapshot]:
    snapshot = await load_team_budget_snapshot(
        root_run_id=root_run_id,
        repository=repository,
        budget_repository=budget_repository,
        now=now,
    )
    decision = evaluate_single_budget(
        snapshot.to_ledger(),
        policy,
        estimated_prompt_tokens=estimated_prompt_tokens,
        now=now,
    )
    return decision, snapshot


async def ensure_team_budget(
    *,
    run: Run,
    repository: RuntimeRepository,
    budget_repository: BudgetRepository | None,
    policy: BudgetPolicy | None,
    now: datetime,
    estimated_prompt_tokens: int = 0,
    event_sink: object | None = None,
    assignment: TeamAssignment | None = None,
    agent_id: UUID | None = None,
) -> TeamBudgetSnapshot | None:
    if budget_repository is None or policy is None:
        return None
    root_run_id = run.root_run_id or run.id
    decision, snapshot = await evaluate_team_budget(
        root_run_id=root_run_id,
        repository=repository,
        budget_repository=budget_repository,
        policy=policy,
        estimated_prompt_tokens=estimated_prompt_tokens,
        now=now,
    )
    if decision is BudgetDecision.EXHAUSTED:
        if callable(event_sink):
            await event_sink(
                EventType.BUDGET_EXHAUSTED,
                {
                    **snapshot.to_event_payload(),
                    **build_team_attribution(
                        run=run,
                        assignment=assignment,
                        agent_id=agent_id,
                    ),
                    "estimated_prompt_tokens": estimated_prompt_tokens,
                    "scope": "team_root",
                },
                f"team-budget-exhausted:{root_run_id}:{snapshot.model_call_count}",
            )
        raise PermanentExecutionError(
            "team_budget_exhausted: root token or wall-clock budget exhausted"
        )
    if decision in {BudgetDecision.COMPACT, BudgetDecision.FINAL_ANSWER} and callable(
        event_sink
    ):
        await event_sink(
            EventType.BUDGET_THRESHOLD_REACHED,
            {
                **snapshot.to_event_payload(),
                **build_team_attribution(
                    run=run,
                    assignment=assignment,
                    agent_id=agent_id,
                ),
                "estimated_prompt_tokens": estimated_prompt_tokens,
                "decision": decision.value,
                "scope": "team_root",
            },
            f"team-budget-threshold:{root_run_id}:{snapshot.model_call_count}",
        )
    return snapshot


def build_team_attribution(
    *,
    run: Run,
    assignment: TeamAssignment | None = None,
    agent_id: UUID | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "root_run_id": str(run.root_run_id or run.id),
        "parent_run_id": str(run.parent_run_id) if run.parent_run_id else None,
        "child_run_id": str(run.id),
        "depth": run.depth,
        "child_role": run.child_role,
    }
    if assignment is not None:
        payload.update(
            {
                "assignment_id": str(assignment.id),
                "assignment_kind": assignment.kind.value,
                "role_profile": assignment.role_profile,
            }
        )
    if agent_id is not None:
        payload["agent_id"] = str(agent_id)
    return payload
