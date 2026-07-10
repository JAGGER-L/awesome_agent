from __future__ import annotations

import pytest
from pydantic import ValidationError

from awesome_agent.agent import (
    AgentState,
    TurnBudget,
    add_active_segment,
    budget_exhaustion,
    charge_compression,
    charge_model_attempt,
    charge_tool_call,
    model_call_decision,
    new_agent_state,
)


def _state() -> AgentState:
    return new_agent_state(
        thread_id="thread_1",
        turn_id="turn_1",
        workspace_key="workspace_1",
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        thinking_enabled=False,
    )


def test_budget_defaults_and_hard_maxima_are_frozen() -> None:
    assert TurnBudget() == TurnBudget(
        model_calls=32,
        tool_calls=64,
        provider_retries=2,
        compressions=2,
        active_execution_seconds=1_800,
    )
    assert (
        TurnBudget(
            model_calls=256,
            tool_calls=512,
            provider_retries=6,
            compressions=10,
            active_execution_seconds=21_600,
        ).model_calls
        == 256
    )

    for field, value in (
        ("model_calls", 257),
        ("tool_calls", 513),
        ("provider_retries", 7),
        ("compressions", 11),
        ("active_execution_seconds", 21_601),
    ):
        with pytest.raises(ValidationError):
            TurnBudget.model_validate({field: value})


def test_model_attempt_charges_initial_call_and_each_gateway_retry() -> None:
    state = charge_model_attempt(_state(), provider_retries=2)

    assert state["model_calls"] == 3
    assert state["provider_retries"] == 2


def test_tool_and_compression_invocations_are_charged_once() -> None:
    state = charge_tool_call(_state())
    state = charge_compression(state)

    assert state["tool_calls"] == 1
    assert state["compressions"] == 1


def test_active_segments_exclude_interaction_wait_time() -> None:
    state = add_active_segment(_state(), started_at=0.0, ended_at=2.0)
    state = add_active_segment(state, started_at=100.0, ended_at=103.0)

    assert state["active_execution_seconds"] == 5.0
    with pytest.raises(ValueError, match="monotonic"):
        add_active_segment(state, started_at=5.0, ended_at=4.0)


def test_last_model_slot_is_reserved_for_tools_disabled_final_call() -> None:
    state = _state()
    state["model_calls"] = 30
    regular = model_call_decision(state, TurnBudget(), tools_requested=True)
    state["model_calls"] = 31
    reserved = model_call_decision(state, TurnBudget(), tools_requested=True)
    state["model_calls"] = 32
    denied = model_call_decision(state, TurnBudget(), tools_requested=False)

    assert regular.allowed and regular.tools_enabled and not regular.reserved_final
    assert reserved.allowed and not reserved.tools_enabled and reserved.reserved_final
    assert not denied.allowed
    assert denied.termination_reason == "model_budget_exhausted"


def test_active_limit_also_uses_reserved_final_call_when_available() -> None:
    state = _state()
    state["active_execution_seconds"] = 1_800

    decision = model_call_decision(state, TurnBudget(), tools_requested=True)

    assert decision.allowed
    assert decision.reserved_final
    assert not decision.tools_enabled


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("tool_calls", 64, "tool_budget_exhausted"),
        ("provider_retries", 2, "provider_retry_budget_exhausted"),
        ("compressions", 2, "compression_budget_exhausted"),
        ("active_execution_seconds", 1_800, "active_time_budget_exhausted"),
    ],
)
def test_budget_exhaustion_reports_the_first_reached_limit(
    field: str,
    value: int,
    reason: str,
) -> None:
    state = _state()
    state[field] = value  # type: ignore[literal-required]

    assert budget_exhaustion(state, TurnBudget()) == reason
