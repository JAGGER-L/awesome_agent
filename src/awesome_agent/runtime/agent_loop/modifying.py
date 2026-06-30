from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import ModelMessage
from awesome_agent.observability.facade import ObservabilityFacade
from awesome_agent.runtime.agent_loop.contracts import (
    CapabilityContext,
    MiddlewareContext,
    MiddlewareStage,
    TokenBudgetContext,
    TraceContext,
)
from awesome_agent.runtime.agent_loop.middleware import MiddlewareStack
from awesome_agent.runtime.agent_loop.observability_middleware import (
    ObservabilityMiddleware,
)

StateT = TypeVar("StateT")


class ModifyingAgentLoop:
    def __init__(
        self,
        *,
        middleware_stack: MiddlewareStack | None = None,
        observability: ObservabilityFacade | None = None,
    ) -> None:
        middleware = list(middleware_stack.middleware) if middleware_stack else []
        if observability is not None:
            middleware.append(ObservabilityMiddleware(observability))
        self.middleware_stack = MiddlewareStack(middleware)

    async def before_agent(
        self,
        state: StateT,
        *,
        run: Run,
        agent: Agent,
        messages: list[ModelMessage],
        handler: Callable[[StateT], Awaitable[StateT]],
    ) -> StateT:
        return await self._run_stage(
            MiddlewareStage.BEFORE_AGENT,
            state,
            run=run,
            agent=agent,
            messages=messages,
            handler=handler,
        )

    async def before_model(
        self,
        state: StateT,
        *,
        run: Run,
        agent: Agent,
        messages: list[ModelMessage],
        handler: Callable[[StateT], Awaitable[StateT]],
    ) -> StateT:
        return await self._run_stage(
            MiddlewareStage.BEFORE_MODEL,
            state,
            run=run,
            agent=agent,
            messages=messages,
            handler=handler,
        )

    async def wrap_model_call(
        self,
        state: StateT,
        *,
        run: Run,
        agent: Agent,
        messages: list[ModelMessage],
        handler: Callable[[StateT], Awaitable[StateT]],
    ) -> StateT:
        return await self._run_stage(
            MiddlewareStage.WRAP_MODEL_CALL,
            state,
            run=run,
            agent=agent,
            messages=messages,
            handler=handler,
        )

    async def after_model(
        self,
        state: StateT,
        *,
        run: Run,
        agent: Agent,
        messages: list[ModelMessage],
        handler: Callable[[StateT], Awaitable[StateT]],
    ) -> StateT:
        return await self._run_stage(
            MiddlewareStage.AFTER_MODEL,
            state,
            run=run,
            agent=agent,
            messages=messages,
            handler=handler,
        )

    async def wrap_tool_call(
        self,
        state: StateT,
        *,
        run: Run,
        agent: Agent,
        messages: list[ModelMessage],
        handler: Callable[[StateT], Awaitable[StateT]],
    ) -> StateT:
        return await self._run_stage(
            MiddlewareStage.WRAP_TOOL_CALL,
            state,
            run=run,
            agent=agent,
            messages=messages,
            handler=handler,
        )

    async def after_agent(
        self,
        state: StateT,
        *,
        run: Run,
        agent: Agent,
        messages: list[ModelMessage],
        handler: Callable[[StateT], Awaitable[StateT]],
    ) -> StateT:
        return await self._run_stage(
            MiddlewareStage.AFTER_AGENT,
            state,
            run=run,
            agent=agent,
            messages=messages,
            handler=handler,
        )

    async def _run_stage(
        self,
        stage: MiddlewareStage,
        state: StateT,
        *,
        run: Run,
        agent: Agent,
        messages: list[ModelMessage],
        handler: Callable[[StateT], Awaitable[StateT]],
    ) -> StateT:
        context = MiddlewareContext(
            run_id=str(run.id),
            agent_id=str(agent.id),
            runtime_route=run.runtime_route or "",
            messages=messages,
            metadata={"stage": stage.value},
            trace=TraceContext(
                run_id=str(run.id),
                parent_run_id=str(run.parent_run_id) if run.parent_run_id else None,
                trace_id=str(run.root_run_id or run.id),
                runtime_route=run.runtime_route or "",
            ),
            capabilities=CapabilityContext(
                subject_id=str(agent.id),
                subject_kind=agent.kind.value,
                policy_id=None,
                allowed_tool_names=(),
            ),
            budget=TokenBudgetContext(token_limit=None),
        )

        async def operation() -> StateT:
            decision = await self.middleware_stack.run_stage(stage, context)
            if not decision.continue_loop:
                reason = decision.reason or f"{stage.value} stopped the agent loop"
                raise RuntimeError(reason)
            return await handler(state)

        return await self.middleware_stack.run_operation(stage, context, operation)
