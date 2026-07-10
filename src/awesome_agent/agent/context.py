from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
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
from awesome_agent.modeling import (
    GatewayEvent,
    ModelGateway,
    ModelMessage,
    ModelUsage,
)


@dataclass(frozen=True, slots=True)
class PreparedAgentContext:
    messages: tuple[ModelMessage, ...]
    manifest: tuple[dict[str, JsonValue], ...]
    estimated_input_tokens: int = 0
    effective_input_limit: int = 1_000_000_000
    compression_recommended: bool = False


@dataclass(frozen=True, slots=True)
class AgentCompressionResult:
    completed: bool
    attempted: bool
    prepared: PreparedAgentContext | None = None
    usage: ModelUsage = field(default_factory=ModelUsage)
    error_code: str | None = None


class AgentContextCompressor(Protocol):
    async def compress(self, state: AgentState) -> AgentCompressionResult: ...


class DisabledAgentContextCompressor:
    async def compress(self, state: AgentState) -> AgentCompressionResult:
        del state
        return AgentCompressionResult(completed=False, attempted=False)


class AgentEventProjector(Protocol):
    async def project_gateway(self, event: GatewayEvent) -> None: ...
    async def project_tool(self, result: ToolResult) -> None: ...
    async def project_context(
        self,
        *,
        source_count: int,
        estimated_tokens: int,
        compressed: bool,
    ) -> None: ...
    async def project_warning(self, *, code: str, message: str) -> None: ...


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
    compressor: AgentContextCompressor = field(
        default_factory=DisabledAgentContextCompressor
    )
