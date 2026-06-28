from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from awesome_agent.modeling import SystemMessage
from awesome_agent.runtime.agent_loop import (
    AgentLoopStatus,
    MiddlewareContext,
    MiddlewareDecision,
    MiddlewareStack,
    MiddlewareStage,
)


class RecordingMiddleware:
    def __init__(
        self,
        name: str,
        events: list[str],
        *,
        stop: bool = False,
    ) -> None:
        self.name = name
        self.events = events
        self.stop = stop

    async def handle(
        self,
        stage: MiddlewareStage,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[MiddlewareDecision]],
    ) -> MiddlewareDecision:
        self.events.append(f"{self.name}:enter:{stage.value}")
        if self.stop:
            self.events.append(f"{self.name}:stop:{context.runtime_route}")
            return MiddlewareDecision.stop(AgentLoopStatus.FAILED, "stopped")
        decision = await call_next(context)
        self.events.append(f"{self.name}:exit:{stage.value}")
        return decision


@pytest.mark.asyncio
async def test_middleware_stack_runs_in_registration_order() -> None:
    events: list[str] = []
    stack = MiddlewareStack(
        [
            RecordingMiddleware("first", events),
            RecordingMiddleware("second", events),
        ]
    )

    decision = await stack.run_stage(
        MiddlewareStage.BEFORE_MODEL,
        MiddlewareContext(
            run_id="run",
            agent_id="agent",
            runtime_route="solo-readonly",
            messages=[SystemMessage(content="hello")],
        ),
    )

    assert decision.continue_loop
    assert events == [
        "first:enter:before_model",
        "second:enter:before_model",
        "second:exit:before_model",
        "first:exit:before_model",
    ]


@pytest.mark.asyncio
async def test_middleware_stack_can_short_circuit_stage() -> None:
    events: list[str] = []
    stack = MiddlewareStack(
        [
            RecordingMiddleware("first", events),
            RecordingMiddleware("second", events, stop=True),
            RecordingMiddleware("third", events),
        ]
    )

    decision = await stack.run_stage(
        MiddlewareStage.WRAP_MODEL_CALL,
        MiddlewareContext(
            run_id="run",
            agent_id="agent",
            runtime_route="solo-readonly",
            messages=[SystemMessage(content="hello")],
        ),
    )

    assert not decision.continue_loop
    assert decision.status is AgentLoopStatus.FAILED
    assert decision.reason == "stopped"
    assert events == [
        "first:enter:wrap_model_call",
        "second:enter:wrap_model_call",
        "second:stop:solo-readonly",
        "first:exit:wrap_model_call",
    ]
