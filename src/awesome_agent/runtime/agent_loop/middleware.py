from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from awesome_agent.runtime.agent_loop.contracts import (
    MiddlewareContext,
    MiddlewareDecision,
    MiddlewareStage,
)


class AgentLoopMiddleware(Protocol):
    name: str

    async def handle(
        self,
        stage: MiddlewareStage,
        context: MiddlewareContext,
        call_next: Callable[
            [MiddlewareContext],
            Awaitable[MiddlewareDecision],
        ],
    ) -> MiddlewareDecision:
        """Handle one middleware stage and call the next middleware if needed."""
        ...


class MiddlewareStack:
    def __init__(self, middleware: list[AgentLoopMiddleware] | None = None) -> None:
        self._middleware = list(middleware or [])

    @property
    def middleware(self) -> tuple[AgentLoopMiddleware, ...]:
        return tuple(self._middleware)

    async def run_stage(
        self,
        stage: MiddlewareStage,
        context: MiddlewareContext,
    ) -> MiddlewareDecision:
        handler_type = Callable[[MiddlewareContext], Awaitable[MiddlewareDecision]]

        async def terminal(_: MiddlewareContext) -> MiddlewareDecision:
            return MiddlewareDecision.continue_()

        call_next: handler_type = terminal
        for middleware in reversed(self._middleware):
            next_handler = call_next

            async def handler(
                current_context: MiddlewareContext,
                *,
                current_middleware: AgentLoopMiddleware = middleware,
                current_next: Callable[
                    [MiddlewareContext],
                    Awaitable[MiddlewareDecision],
                ] = next_handler,
            ) -> MiddlewareDecision:
                return await current_middleware.handle(
                    stage,
                    current_context,
                    current_next,
                )

            call_next = handler
        return await call_next(context)
