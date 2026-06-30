from __future__ import annotations

from datetime import UTC, datetime

from awesome_agent.modeling import ModelRequest, ToolDefinition, UserMessage
from awesome_agent.runtime.budget import (
    BudgetDecision,
    BudgetLedger,
    BudgetPolicy,
    estimate_model_request_tokens,
    estimate_tokens,
    evaluate_budget,
)


def test_estimate_tokens_is_conservative_for_code_and_chinese() -> None:
    assert estimate_tokens("abc" * 30) >= 30
    assert estimate_tokens("你好" * 20) >= 20


def test_estimate_model_request_tokens_counts_tools() -> None:
    request_without_tools = ModelRequest(messages=[UserMessage(content="inspect")])
    request_with_tools = ModelRequest(
        messages=[UserMessage(content="inspect")],
        tools=[
            ToolDefinition(
                name="repo.read",
                description="Read a repository file.",
                input_schema={"type": "object"},
            )
        ],
    )

    assert estimate_model_request_tokens(request_with_tools) > (
        estimate_model_request_tokens(request_without_tools)
    )


def test_soft_context_limit_requests_compaction() -> None:
    decision = evaluate_budget(
        BudgetLedger(),
        BudgetPolicy(
            soft_context_tokens=100,
            hard_context_tokens=200,
            recent_context_tokens=80,
            max_total_tokens_per_run=10_000,
            max_reasoning_tokens_per_run=5_000,
            max_active_seconds_per_run=3600,
        ),
        estimated_prompt_tokens=125,
        now=datetime.now(UTC),
    )

    assert decision is BudgetDecision.COMPACT


def test_hard_total_token_limit_fails_run() -> None:
    ledger = BudgetLedger(total_input_tokens=900, total_output_tokens=200)
    policy = BudgetPolicy(
        soft_context_tokens=10_000,
        hard_context_tokens=20_000,
        recent_context_tokens=8_000,
        max_total_tokens_per_run=1_000,
        max_reasoning_tokens_per_run=5_000,
        max_active_seconds_per_run=3600,
    )

    decision = evaluate_budget(
        ledger,
        policy,
        estimated_prompt_tokens=500,
        now=datetime.now(UTC),
    )

    assert decision is BudgetDecision.EXHAUSTED


def test_active_wall_clock_excludes_paused_time() -> None:
    ledger = BudgetLedger(active_seconds=55)
    opened = ledger.open_active_window(datetime(2026, 1, 1, tzinfo=UTC))
    closed = opened.close_active_window(datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC))

    assert closed.active_seconds == 65
