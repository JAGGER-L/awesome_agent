from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.agent.state import AgentState


class TurnBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_calls: int = Field(default=32, ge=1, le=256)
    tool_calls: int = Field(default=64, ge=1, le=512)
    provider_retries: int = Field(default=2, ge=0, le=6)
    compressions: int = Field(default=2, ge=0, le=10)
    active_execution_seconds: float = Field(default=1_800, gt=0, le=21_600)


class BudgetDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    tools_enabled: bool = False
    reserved_final: bool = False
    termination_reason: str | None = None


def model_call_decision(
    state: AgentState,
    budget: TurnBudget,
    *,
    tools_requested: bool,
) -> BudgetDecision:
    model_calls = state["model_calls"]
    if model_calls >= budget.model_calls:
        return BudgetDecision(
            allowed=False,
            termination_reason="model_budget_exhausted",
        )
    active_exhausted = (
        state["active_execution_seconds"] >= budget.active_execution_seconds
    )
    reserved = active_exhausted or model_calls == budget.model_calls - 1
    return BudgetDecision(
        allowed=True,
        tools_enabled=tools_requested and not reserved,
        reserved_final=reserved,
    )


def budget_exhaustion(state: AgentState, budget: TurnBudget) -> str | None:
    if state["model_calls"] >= budget.model_calls:
        return "model_budget_exhausted"
    if state["tool_calls"] >= budget.tool_calls:
        return "tool_budget_exhausted"
    if state["provider_retries"] >= budget.provider_retries:
        return "provider_retry_budget_exhausted"
    if state["compressions"] >= budget.compressions:
        return "compression_budget_exhausted"
    if state["active_execution_seconds"] >= budget.active_execution_seconds:
        return "active_time_budget_exhausted"
    return None


def charge_model_attempt(
    state: AgentState,
    *,
    provider_retries: int,
) -> AgentState:
    if provider_retries < 0:
        raise ValueError("provider_retries cannot be negative")
    updated = _copy(state)
    updated["model_calls"] += 1 + provider_retries
    updated["provider_retries"] += provider_retries
    return updated


def charge_tool_call(state: AgentState) -> AgentState:
    updated = _copy(state)
    updated["tool_calls"] += 1
    return updated


def charge_compression(state: AgentState) -> AgentState:
    updated = _copy(state)
    updated["compressions"] += 1
    return updated


def add_active_segment(
    state: AgentState,
    *,
    started_at: float,
    ended_at: float,
) -> AgentState:
    if ended_at < started_at:
        raise ValueError("monotonic segment end cannot precede start")
    updated = _copy(state)
    updated["active_execution_seconds"] += ended_at - started_at
    return updated


def _copy(state: AgentState) -> AgentState:
    return cast(AgentState, dict(state))
