from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from math import ceil

from awesome_agent.modeling.messages import AssistantMessage, ModelMessage


class BudgetDecision(StrEnum):
    WITHIN_BUDGET = "within_budget"
    COMPACT = "compact"
    FINAL_ANSWER = "final_answer"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    soft_context_tokens: int
    hard_context_tokens: int
    recent_context_tokens: int
    max_total_tokens_per_run: int
    max_reasoning_tokens_per_run: int
    max_active_seconds_per_run: int


@dataclass(frozen=True, slots=True)
class TokenUsageDelta:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True, slots=True)
class BudgetLedger:
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_reasoning_tokens: int = 0
    active_seconds: int = 0
    model_call_count: int = 0
    threshold_status: str = BudgetDecision.WITHIN_BUDGET.value
    active_window_started_at: datetime | None = None

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def add_usage(self, usage: TokenUsageDelta) -> BudgetLedger:
        return replace(
            self,
            total_input_tokens=self.total_input_tokens + usage.input_tokens,
            total_output_tokens=self.total_output_tokens + usage.output_tokens,
            total_reasoning_tokens=(
                self.total_reasoning_tokens + usage.reasoning_tokens
            ),
            model_call_count=self.model_call_count + 1,
        )

    def open_active_window(self, now: datetime) -> BudgetLedger:
        if self.active_window_started_at is not None:
            return self
        return replace(self, active_window_started_at=now)

    def close_active_window(self, now: datetime) -> BudgetLedger:
        if self.active_window_started_at is None:
            return self
        elapsed = max(0, int((now - self.active_window_started_at).total_seconds()))
        return replace(
            self,
            active_seconds=self.active_seconds + elapsed,
            active_window_started_at=None,
        )

    def active_seconds_at(self, now: datetime) -> int:
        if self.active_window_started_at is None:
            return self.active_seconds
        elapsed = max(0, int((now - self.active_window_started_at).total_seconds()))
        return self.active_seconds + elapsed


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    non_ascii = sum(1 for char in text if ord(char) > 127)
    ascii_chars = len(text) - non_ascii
    return ceil(ascii_chars / 3) + non_ascii


def estimate_messages_tokens(messages: Sequence[ModelMessage]) -> int:
    total = 0
    for message in messages:
        total += estimate_tokens(getattr(message, "content", ""))
        if isinstance(message, AssistantMessage):
            total += sum(
                estimate_tokens(call.name) + estimate_tokens(call.arguments_json)
                for call in message.tool_calls
            )
    return total


def evaluate_budget(
    ledger: BudgetLedger,
    policy: BudgetPolicy,
    *,
    estimated_prompt_tokens: int,
    now: datetime,
) -> BudgetDecision:
    if ledger.total_tokens + estimated_prompt_tokens > policy.max_total_tokens_per_run:
        return BudgetDecision.EXHAUSTED
    if ledger.total_reasoning_tokens > policy.max_reasoning_tokens_per_run:
        return BudgetDecision.EXHAUSTED
    if ledger.active_seconds_at(now) > policy.max_active_seconds_per_run:
        return BudgetDecision.EXHAUSTED
    if estimated_prompt_tokens >= policy.hard_context_tokens:
        return BudgetDecision.FINAL_ANSWER
    if estimated_prompt_tokens >= policy.soft_context_tokens:
        return BudgetDecision.COMPACT
    return BudgetDecision.WITHIN_BUDGET
