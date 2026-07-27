from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import JsonValue

from awesome_agent.agent.budgets import TurnBudget
from awesome_agent.agent.finalization import (
    DisabledPostAnswerFinalizer,
    PostAnswerFinalizer,
)
from awesome_agent.agent.state import AgentState
from awesome_agent.core.tools import (
    ToolExecutionContext,
    ToolExecutor,
    ToolRequest,
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
    async def compress(
        self,
        state: AgentState,
        *,
        max_provider_retries: int,
    ) -> AgentCompressionResult: ...


class DisabledAgentContextCompressor:
    async def compress(
        self,
        state: AgentState,
        *,
        max_provider_retries: int,
    ) -> AgentCompressionResult:
        del state, max_provider_retries
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


async def discard_context_snapshot(
    manifest: tuple[dict[str, JsonValue], ...],
) -> None:
    del manifest


type AgentContextBuilder = Callable[
    [AgentState],
    Awaitable[PreparedAgentContext],
]


@dataclass(frozen=True, slots=True)
class AgentRuntimeContext:
    gateway: ModelGateway
    executor: ToolExecutor
    tool_catalog: Callable[[], tuple[ToolSpec, ...]]
    tool_context_factory: Callable[
        [AgentState, ToolRequest], Awaitable[ToolExecutionContext]
    ]
    event_projector: AgentEventProjector
    context_builder: AgentContextBuilder
    budget: TurnBudget
    monotonic: Callable[[], float]
    context_token_estimator: Callable[[tuple[ModelMessage, ...]], int]
    current_user_text: str = ""
    context_snapshot_recorder: Callable[
        [tuple[dict[str, JsonValue], ...]],
        Awaitable[None],
    ] = discard_context_snapshot
    compressor: AgentContextCompressor = field(
        default_factory=DisabledAgentContextCompressor
    )
    post_answer_finalizer: PostAnswerFinalizer = field(
        default_factory=DisabledPostAnswerFinalizer
    )
