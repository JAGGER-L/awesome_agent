from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from awesome_agent.domain.enums import EventType
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import ModelMessage, ModelTurn, StopReason
from awesome_agent.persistence.budget import (
    BudgetRepository,
    ContextCompactionRecord,
    RunBudgetLedgerRecord,
)
from awesome_agent.runtime.budget import (
    BudgetDecision,
    BudgetLedger,
    BudgetPolicy,
    evaluate_budget,
)
from awesome_agent.runtime.context import ContextManager, ContextPolicy, PreparedContext
from awesome_agent.runtime.dispatch import PermanentExecutionError
from awesome_agent.runtime.token_accounting import (
    TokenAccountant,
    default_token_accountant,
)


class ReadOnlyEvidenceMiddleware:
    def route_turn(
        self,
        *,
        turn: ModelTurn,
        force_final: bool,
        successful_inspections: int,
    ) -> str:
        if turn.assistant.tool_calls:
            if force_final:
                return "feedback"
            return "tools"
        if (
            turn.stop_reason is StopReason.COMPLETED
            and bool(turn.assistant.content.strip())
            and successful_inspections > 0
        ):
            return "finalize"
        return "feedback"


class ReadOnlyProgressMiddleware:
    def budget_reminder(
        self,
        *,
        next_count: int,
        max_model_turns: int,
    ) -> str | None:
        ratio = next_count / max_model_turns
        if ratio >= 0.9:
            return (
                "Stop broad exploration. Produce the best evidence-based final "
                "answer soon."
            )
        if ratio >= 0.7:
            return (
                "Start converging. Inspect only evidence still needed for the answer."
            )
        return None


class ReadOnlyContextMiddleware:
    def __init__(
        self,
        *,
        context_manager: ContextManager | None,
        budget_repository: BudgetRepository | None,
        budget_policy: BudgetPolicy | None,
        runtime_route: str,
    ) -> None:
        self.context_manager = context_manager
        self.budget_repository = budget_repository
        self.budget_policy = budget_policy
        self.runtime_route = runtime_route

    async def prepare_context(
        self,
        *,
        run: Run,
        agent: Agent,
        messages: list[ModelMessage],
        rolling_summary: str,
    ) -> PreparedContext | None:
        if self.context_manager is None or self.budget_policy is None:
            return None
        return await self.context_manager.prepare_request(
            run_id=run.id,
            agent_id=agent.id,
            runtime_route=self.runtime_route,
            messages=messages,
            rolling_summary=rolling_summary,
            policy=ContextPolicy(
                soft_context_tokens=self.budget_policy.soft_context_tokens,
                hard_context_tokens=self.budget_policy.hard_context_tokens,
                recent_context_tokens=self.budget_policy.recent_context_tokens,
            ),
        )

    async def record_compaction(
        self,
        *,
        run: Run,
        agent: Agent,
        prepared: PreparedContext,
    ) -> None:
        if self.budget_repository is None:
            return
        artifact_refs = _uuid_artifact_refs(prepared.artifact_refs)
        await self.budget_repository.record_compaction(
            ContextCompactionRecord(
                run_id=run.id,
                agent_id=agent.id,
                runtime_route=self.runtime_route,
                before_estimated_tokens=prepared.before_estimated_tokens,
                after_estimated_tokens=prepared.after_estimated_tokens,
                summary=prepared.rolling_summary,
                artifact_refs=artifact_refs,
            )
        )


class BudgetExhausted(PermanentExecutionError):
    pass


class ReadOnlyBudgetMiddleware:
    def __init__(
        self,
        *,
        budget_repository: BudgetRepository | None,
        budget_policy: BudgetPolicy | None,
        emit: Any,
        token_accountant: TokenAccountant | None = None,
    ) -> None:
        self.budget_repository = budget_repository
        self.budget_policy = budget_policy
        self.emit = emit
        self.token_accountant = token_accountant or default_token_accountant()

    async def load_ledger(
        self,
        run_id: UUID,
        state_payload: dict[str, Any],
    ) -> BudgetLedger:
        if self.budget_repository is not None:
            record = await self.budget_repository.get_ledger(run_id)
            return _ledger_from_record(record)
        return _ledger_from_state(state_payload)

    async def persist_ledger(
        self,
        run_id: UUID,
        ledger: BudgetLedger,
    ) -> None:
        if self.budget_repository is not None:
            await self.budget_repository.upsert_ledger(
                _record_from_ledger(run_id, ledger)
            )

    async def evaluate_before_model_call(
        self,
        *,
        run_id: UUID,
        ledger: BudgetLedger,
        request_messages: list[ModelMessage],
        before_estimated_tokens: int,
        turn: int,
    ) -> BudgetLedger:
        if self.budget_policy is None:
            return ledger
        now = datetime.now(UTC)
        before_decision = evaluate_budget(
            ledger,
            self.budget_policy,
            estimated_prompt_tokens=before_estimated_tokens,
            now=now,
        )
        estimated_prompt_tokens = self.token_accountant.estimate_messages(
            request_messages
        ).tokens
        decision = evaluate_budget(
            ledger,
            self.budget_policy,
            estimated_prompt_tokens=estimated_prompt_tokens,
            now=now,
        )
        threshold_status = decision.value
        if decision is BudgetDecision.WITHIN_BUDGET and before_decision in {
            BudgetDecision.COMPACT,
            BudgetDecision.FINAL_ANSWER,
        }:
            threshold_status = before_decision.value
            await self.emit(
                EventType.BUDGET_THRESHOLD_REACHED,
                {
                    "turn": turn,
                    "decision": before_decision.value,
                    "before_estimated_tokens": before_estimated_tokens,
                    "estimated_prompt_tokens": estimated_prompt_tokens,
                },
                f"budget-threshold:{turn}",
            )
        elif decision in {BudgetDecision.COMPACT, BudgetDecision.FINAL_ANSWER}:
            await self.emit(
                EventType.BUDGET_THRESHOLD_REACHED,
                {
                    "turn": turn,
                    "decision": decision.value,
                    "before_estimated_tokens": before_estimated_tokens,
                    "estimated_prompt_tokens": estimated_prompt_tokens,
                },
                f"budget-threshold:{turn}",
            )
        updated = replace(ledger, threshold_status=threshold_status)
        await self.persist_ledger(run_id, updated)
        if decision is BudgetDecision.EXHAUSTED:
            exhausted = replace(
                updated,
                threshold_status=BudgetDecision.EXHAUSTED.value,
            )
            await self.persist_ledger(run_id, exhausted)
            await self.emit(
                EventType.BUDGET_EXHAUSTED,
                {
                    "turn": turn,
                    "estimated_prompt_tokens": estimated_prompt_tokens,
                    "total_tokens": exhausted.total_tokens,
                    "reasoning_tokens": exhausted.total_reasoning_tokens,
                    "active_seconds": exhausted.active_seconds_at(now),
                },
                f"budget-exhausted:{turn}",
            )
            raise BudgetExhausted(
                "budget_exhausted: token or wall-clock budget exhausted"
            )
        return updated


def ledger_to_state(ledger: BudgetLedger) -> dict[str, Any]:
    return {
        "total_input_tokens": ledger.total_input_tokens,
        "total_output_tokens": ledger.total_output_tokens,
        "total_reasoning_tokens": ledger.total_reasoning_tokens,
        "active_seconds": ledger.active_seconds,
        "model_call_count": ledger.model_call_count,
        "threshold_status": ledger.threshold_status,
        "active_window_started_at": (
            ledger.active_window_started_at.isoformat()
            if ledger.active_window_started_at is not None
            else None
        ),
    }


def _ledger_from_state(payload: dict[str, Any]) -> BudgetLedger:
    return BudgetLedger(
        total_input_tokens=int(payload.get("total_input_tokens", 0)),
        total_output_tokens=int(payload.get("total_output_tokens", 0)),
        total_reasoning_tokens=int(payload.get("total_reasoning_tokens", 0)),
        active_seconds=int(payload.get("active_seconds", 0)),
        model_call_count=int(payload.get("model_call_count", 0)),
        threshold_status=str(
            payload.get("threshold_status", BudgetDecision.WITHIN_BUDGET.value)
        ),
    )


def _ledger_from_record(record: RunBudgetLedgerRecord) -> BudgetLedger:
    return BudgetLedger(
        total_input_tokens=record.total_input_tokens,
        total_output_tokens=record.total_output_tokens,
        total_reasoning_tokens=record.total_reasoning_tokens,
        active_seconds=record.active_seconds,
        model_call_count=record.model_call_count,
        threshold_status=record.threshold_status,
        active_window_started_at=record.active_window_started_at,
    )


def _record_from_ledger(
    run_id: UUID,
    ledger: BudgetLedger,
) -> RunBudgetLedgerRecord:
    return RunBudgetLedgerRecord(
        run_id=run_id,
        total_input_tokens=ledger.total_input_tokens,
        total_output_tokens=ledger.total_output_tokens,
        total_reasoning_tokens=ledger.total_reasoning_tokens,
        active_seconds=ledger.active_seconds,
        model_call_count=ledger.model_call_count,
        threshold_status=ledger.threshold_status,
        active_window_started_at=ledger.active_window_started_at,
    )


def _uuid_artifact_refs(artifact_refs: list[str]) -> list[UUID]:
    refs: list[UUID] = []
    for artifact_ref in artifact_refs:
        try:
            refs.append(UUID(artifact_ref))
        except ValueError:
            continue
    return refs
