from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest

from awesome_agent.domain.enums import AgentKind, RunIntent, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import (
    AssistantMessage,
    ModelMessage,
    ModelTurn,
    ModelUsage,
    StopReason,
    ToolCall,
    UserMessage,
)
from awesome_agent.observability.facade import NoopObservabilityFacade
from awesome_agent.runtime.agent_loop.contracts import (
    MiddlewareContext,
    MiddlewareDecision,
    MiddlewareStage,
)
from awesome_agent.runtime.agent_loop.middleware import MiddlewareStack
from awesome_agent.runtime.agent_loop.observability_middleware import (
    ObservabilityMiddleware,
)
from awesome_agent.runtime.agent_loop.read_only import ReadOnlyAgentLoop
from awesome_agent.runtime.agent_loop.read_only_middleware import (
    BudgetExhausted,
    ReadOnlyBudgetMiddleware,
    ReadOnlyEvidenceMiddleware,
    ReadOnlyProgressMiddleware,
)
from awesome_agent.runtime.budget import BudgetLedger, BudgetPolicy
from awesome_agent.runtime.token_accounting import ModelTokenProfile, TokenAccountant


class CharacterTokenizer:
    def count_text(self, text: str) -> int:
        return len(text)


def _character_accountant() -> TokenAccountant:
    return TokenAccountant(
        profiles=[
            ModelTokenProfile(
                provider="unknown",
                model_pattern="*",
                estimator_name="character-tokenizer",
                tokenizer=CharacterTokenizer(),
                message_overhead_tokens=0,
                request_overhead_tokens=0,
                tool_overhead_tokens=0,
                error_margin_ratio=0,
            )
        ]
    )


class RecordingMiddleware:
    name = "recording"

    def __init__(self) -> None:
        self.contexts: list[MiddlewareContext] = []

    async def handle(
        self,
        stage: MiddlewareStage,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[MiddlewareDecision]],
    ) -> MiddlewareDecision:
        self.contexts.append(context)
        return await call_next(context)


def _run() -> Run:
    return Run(
        goal="inspect code",
        mode=RunMode.SOLO,
        intent=RunIntent.READ_ONLY,
        runtime_route="solo-readonly",
    )


def _agent(run: Run) -> Agent:
    return Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake-model",
    )


def test_readonly_agent_loop_installs_observability_middleware() -> None:
    loop = ReadOnlyAgentLoop(observability=NoopObservabilityFacade())

    assert any(
        isinstance(middleware, ObservabilityMiddleware)
        for middleware in loop.middleware_stack.middleware
    )


@pytest.mark.asyncio
async def test_readonly_agent_loop_passes_typed_trace_and_budget_context() -> None:
    run = _run()
    agent = _agent(run)
    middleware = RecordingMiddleware()
    loop = ReadOnlyAgentLoop(middleware_stack=MiddlewareStack([middleware]))
    messages: list[ModelMessage] = [UserMessage(content="question")]

    async def handler(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "handled": True}

    result = await loop.before_model(
        {"phase": "start"},
        run=run,
        agent=agent,
        messages=messages,
        handler=handler,
    )

    context = middleware.contexts[0]
    assert result == {"phase": "start", "handled": True}
    assert context.trace is not None
    assert context.trace.run_id == str(run.id)
    assert context.trace.trace_id == str(run.root_run_id or run.id)
    assert context.trace.runtime_route == "solo-readonly"
    assert context.budget is not None
    assert context.budget.token_limit is None
    assert not any(
        hasattr(context.budget, field)
        for field in ("cost", "price", "amount", "usd", "currency")
    )


def test_readonly_evidence_middleware_routes_tool_calls_to_tools() -> None:
    middleware = ReadOnlyEvidenceMiddleware()
    turn = ModelTurn(
        assistant=AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(
                    call_id="call-1",
                    name="repo.read",
                    arguments_json='{"path":"README.md"}',
                )
            ],
        ),
        stop_reason=StopReason.TOOL_CALLS,
        usage=ModelUsage(),
        provider="fake",
        model="fake",
    )

    assert (
        middleware.route_turn(
            turn=turn,
            force_final=False,
            successful_inspections=0,
        )
        == "tools"
    )
    assert (
        middleware.route_turn(
            turn=turn,
            force_final=True,
            successful_inspections=0,
        )
        == "feedback"
    )


def test_readonly_evidence_middleware_requires_successful_inspection() -> None:
    middleware = ReadOnlyEvidenceMiddleware()
    turn = ModelTurn(
        assistant=AssistantMessage(content="Answer with evidence."),
        stop_reason=StopReason.COMPLETED,
        usage=ModelUsage(),
        provider="fake",
        model="fake",
    )

    assert (
        middleware.route_turn(
            turn=turn,
            force_final=False,
            successful_inspections=0,
        )
        == "feedback"
    )
    assert (
        middleware.route_turn(
            turn=turn,
            force_final=False,
            successful_inspections=1,
        )
        == "finalize"
    )


def test_readonly_progress_middleware_emits_convergence_reminders() -> None:
    middleware = ReadOnlyProgressMiddleware()

    assert middleware.budget_reminder(next_count=41, max_model_turns=60) is None
    assert "Start converging" in (
        middleware.budget_reminder(next_count=42, max_model_turns=60) or ""
    )
    assert "Stop broad exploration" in (
        middleware.budget_reminder(next_count=54, max_model_turns=60) or ""
    )


@pytest.mark.asyncio
async def test_readonly_budget_middleware_uses_token_accountant() -> None:
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    middleware = ReadOnlyBudgetMiddleware(
        budget_repository=None,
        budget_policy=BudgetPolicy(
            soft_context_tokens=100,
            hard_context_tokens=200,
            recent_context_tokens=50,
            max_total_tokens_per_run=10,
            max_reasoning_tokens_per_run=100,
            max_active_seconds_per_run=100,
        ),
        emit=emit,
        token_accountant=_character_accountant(),
    )

    with pytest.raises(BudgetExhausted):
        await middleware.evaluate_before_model_call(
            run_id=uuid4(),
            ledger=BudgetLedger(),
            request_messages=[UserMessage(content="12345678901")],
            before_estimated_tokens=0,
            turn=1,
        )

    assert events[-1][2] == "budget-exhausted:1"
