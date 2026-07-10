from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import JsonValue

from awesome_agent.agent.budgets import TurnBudget
from awesome_agent.agent.state import AgentState
from awesome_agent.core.tools import (
    ToolExecutionContext,
    ToolExecutor,
    ToolResult,
    ToolSpec,
)
from awesome_agent.modeling import GatewayEvent, ModelGateway, ModelMessage


@dataclass(frozen=True, slots=True)
class PreparedAgentContext:
    messages: tuple[ModelMessage, ...]
    manifest: tuple[dict[str, JsonValue], ...]


class AgentEventProjector(Protocol):
    async def project_gateway(self, event: GatewayEvent) -> None: ...
    async def project_tool(self, result: ToolResult) -> None: ...


type AgentContextBuilder = Callable[
    [AgentState],
    Awaitable[PreparedAgentContext],
]


@dataclass(frozen=True, slots=True)
class AgentRuntimeContext:
    gateway: ModelGateway
    executor: ToolExecutor
    tool_catalog: Callable[[], tuple[ToolSpec, ...]]
    tool_context_factory: Callable[[AgentState], ToolExecutionContext]
    event_projector: AgentEventProjector
    context_builder: AgentContextBuilder
    budget: TurnBudget
    monotonic: Callable[[], float]
