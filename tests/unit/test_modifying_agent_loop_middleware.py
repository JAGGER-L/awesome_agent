from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from awesome_agent.domain.enums import AgentKind, RunIntent, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import SystemMessage
from awesome_agent.runtime.agent_loop.contracts import (
    MiddlewareContext,
    MiddlewareDecision,
    MiddlewareStage,
)
from awesome_agent.runtime.agent_loop.middleware import MiddlewareStack
from awesome_agent.runtime.agent_loop.modifying import ModifyingAgentLoop


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
