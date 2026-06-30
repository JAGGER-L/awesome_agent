from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from awesome_agent.domain.enums import AgentKind, RunIntent, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import (
    AssistantMessage,
    ModelTurn,
    ModelUsage,
    StopReason,
    SystemMessage,
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
from awesome_agent.runtime.agent_loop.modifying import (
    ModifyingAgentLoop,
)
from awesome_agent.runtime.agent_loop.modifying_middleware import (
    ModifyingBudgetExhausted,
    ModifyingBudgetMiddleware,
    ModifyingContextMiddleware,
    ModifyingEvidenceMiddleware,
    modifying_ledger_to_state,
)
from awesome_agent.runtime.agent_loop.observability_middleware import (
    ObservabilityMiddleware,
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
        self.stages: list[MiddlewareStage] = []
        self.contexts: list[MiddlewareContext] = []

    async def handle(
        self,
        stage: MiddlewareStage,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[MiddlewareDecision]],
    ) -> MiddlewareDecision:
        self.stages.append(stage)
        self.contexts.append(context)
        return await call_next(context)


def _run() -> Run:
    return Run(
        goal="change code",
        mode=RunMode.SOLO,
        intent=RunIntent.MODIFYING,
        runtime_route="solo-modifying",
    )


def _agent(run: Run) -> Agent:
    return Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake-model",
    )


def test_modifying_agent_loop_installs_observability_middleware() -> None:
    loop = ModifyingAgentLoop(observability=NoopObservabilityFacade())

    assert any(
        isinstance(middleware, ObservabilityMiddleware)
        for middleware in loop.middleware_stack.middleware
    )


@pytest.mark.parametrize(
    ("method_name", "stage"),
    [
        ("before_agent", MiddlewareStage.BEFORE_AGENT),
        ("before_model", MiddlewareStage.BEFORE_MODEL),
        ("wrap_model_call", MiddlewareStage.WRAP_MODEL_CALL),
        ("after_model", MiddlewareStage.AFTER_MODEL),
        ("wrap_tool_call", MiddlewareStage.WRAP_TOOL_CALL),
        ("after_agent", MiddlewareStage.AFTER_AGENT),
    ],
)
@pytest.mark.asyncio
async def test_modifying_agent_loop_runs_stage_before_handler(
    method_name: str,
    stage: MiddlewareStage,
) -> None:
    run = _run()
    agent = _agent(run)
    middleware = RecordingMiddleware()
    loop = ModifyingAgentLoop(middleware_stack=MiddlewareStack([middleware]))
    calls: list[dict[str, Any]] = []
    messages = [SystemMessage(content="system")]

    async def handler(state: dict[str, Any]) -> dict[str, Any]:
        calls.append(state)
        return {**state, "handled": True}

    result = await getattr(loop, method_name)(
        {"phase": "start"},
        run=run,
        agent=agent,
        messages=messages,
        handler=handler,
    )

    assert result == {"phase": "start", "handled": True}
    assert calls == [{"phase": "start"}]
    assert middleware.stages == [stage]
    assert middleware.contexts[0].run_id == str(run.id)
    assert middleware.contexts[0].agent_id == str(agent.id)
    assert middleware.contexts[0].runtime_route == "solo-modifying"
    assert middleware.contexts[0].messages == messages
    assert middleware.contexts[0].metadata == {"stage": stage.value}
    assert middleware.contexts[0].trace is not None
    assert middleware.contexts[0].trace.run_id == str(run.id)
    assert middleware.contexts[0].trace.runtime_route == "solo-modifying"
    assert middleware.contexts[0].capabilities is not None
    assert middleware.contexts[0].capabilities.subject_id == str(agent.id)
    assert middleware.contexts[0].capabilities.subject_kind == "leader"
    assert middleware.contexts[0].capabilities.allowed_tool_names == ()
    assert middleware.contexts[0].budget is not None
    assert not any(
        hasattr(middleware.contexts[0].budget, field)
        for field in ("cost", "price", "amount", "usd", "currency")
    )


@pytest.mark.asyncio
async def test_modifying_context_middleware_returns_none_without_context_manager() -> (
    None
):
    run = _run()
    agent = _agent(run)
    middleware = ModifyingContextMiddleware(
        context_manager=None,
        budget_repository=None,
        budget_policy=None,
        runtime_route="solo-modifying",
    )

    prepared = await middleware.prepare_context(
        run=run,
        agent=agent,
        messages=[SystemMessage(content="system")],
        rolling_summary="",
    )

    assert prepared is None


@pytest.mark.asyncio
async def test_modifying_budget_middleware_loads_ledger_from_state() -> None:
    middleware = ModifyingBudgetMiddleware(
        budget_repository=None,
        budget_policy=None,
        emit=_unused_emit,
    )

    ledger = await middleware.load_ledger(
        _run().id,
        {
            "total_input_tokens": 3,
            "total_output_tokens": 4,
            "total_reasoning_tokens": 5,
            "active_seconds": 6,
            "model_call_count": 7,
            "threshold_status": "compact",
        },
    )

    assert ledger.total_input_tokens == 3
    assert ledger.total_output_tokens == 4
    assert ledger.total_reasoning_tokens == 5
    assert ledger.active_seconds == 6
    assert ledger.model_call_count == 7
    assert ledger.threshold_status == "compact"
    assert modifying_ledger_to_state(ledger)["model_call_count"] == 7


@pytest.mark.asyncio
async def test_modifying_budget_middleware_raises_when_exhausted() -> None:
    events: list[tuple[str, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((str(event_type), payload, transition_id))

    middleware = ModifyingBudgetMiddleware(
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
    )

    with pytest.raises(ModifyingBudgetExhausted):
        await middleware.evaluate_before_model_call(
            run_id=_run().id,
            ledger=BudgetLedger(total_input_tokens=10),
            request_messages=[SystemMessage(content="more tokens")],
            before_estimated_tokens=1,
            turn=1,
        )

    assert events[-1][2] == "budget-exhausted:1"


@pytest.mark.asyncio
async def test_modifying_budget_middleware_uses_token_accountant() -> None:
    events: list[tuple[str, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((str(event_type), payload, transition_id))

    middleware = ModifyingBudgetMiddleware(
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

    with pytest.raises(ModifyingBudgetExhausted):
        await middleware.evaluate_before_model_call(
            run_id=_run().id,
            ledger=BudgetLedger(),
            request_messages=[UserMessage(content="12345678901")],
            before_estimated_tokens=0,
            turn=1,
        )

    assert events[-1][2] == "budget-exhausted:1"


async def _unused_emit(
    event_type: object,
    payload: dict[str, object],
    transition_id: str,
) -> None:
    raise AssertionError("emit should not be called")


def test_modifying_evidence_middleware_routes_tool_calls_and_completion() -> None:
    middleware = ModifyingEvidenceMiddleware(failure_factory=RuntimeError)
    tool_turn = ModelTurn(
        assistant=AssistantMessage(
            tool_calls=[
                ToolCall(
                    call_id="call-1",
                    name="repo.diff",
                    arguments_json="{}",
                )
            ]
        ),
        stop_reason=StopReason.TOOL_CALLS,
        usage=ModelUsage(),
        provider="fake",
        model="fake",
    )
    final_turn = ModelTurn(
        assistant=AssistantMessage(content="Done."),
        stop_reason=StopReason.COMPLETED,
        usage=ModelUsage(),
        provider="fake",
        model="fake",
    )

    assert (
        middleware.route_turn(
            turn=tool_turn,
            force_final=False,
            successful_writes=0,
            final_diff_after_write=False,
        )
        == "tool"
    )
    assert (
        middleware.route_turn(
            turn=tool_turn,
            force_final=True,
            successful_writes=0,
            final_diff_after_write=False,
        )
        == "feedback"
    )
    assert (
        middleware.route_turn(
            turn=final_turn,
            force_final=False,
            successful_writes=1,
            final_diff_after_write=True,
        )
        == "validate"
    )
    assert (
        middleware.route_turn(
            turn=final_turn,
            force_final=False,
            successful_writes=1,
            final_diff_after_write=False,
        )
        == "feedback"
    )
