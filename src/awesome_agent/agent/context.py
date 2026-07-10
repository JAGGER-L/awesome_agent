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
from awesome_agent.memory.distiller import DistillationResult, DistillationStatus
from awesome_agent.memory.identity import Mem0Identity
from awesome_agent.memory.mem0_cloud import Mem0CloudAdapter, Mem0CloudError
from awesome_agent.memory.models import Mem0Diagnostic
from awesome_agent.modeling import (
    GatewayEvent,
    ModelGateway,
    ModelMessage,
    ModelUsage,
    SelectedModel,
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
    async def project_memory_status(self, *, enabled: bool, status: str) -> None: ...


class MemoryDistillation(Protocol):
    async def distill(
        self,
        *,
        user_text: str,
        final_answer: str,
        selected_model: SelectedModel,
        remaining_model_calls: int,
        remaining_provider_retries: int = 6,
        workspace_key: str,
    ) -> DistillationResult: ...


@dataclass(frozen=True, slots=True)
class MemoryFinalizationResult:
    enabled: bool
    status: str
    usage: ModelUsage = field(default_factory=ModelUsage)
    model_calls: int = 0
    diagnostics: tuple[Mem0Diagnostic, ...] = ()


class PostAnswerMemory(Protocol):
    async def finalize(
        self,
        *,
        user_text: str,
        final_answer: str,
        selected_model: SelectedModel,
        remaining_model_calls: int,
        remaining_provider_retries: int,
        workspace_key: str,
    ) -> MemoryFinalizationResult: ...


class DisabledPostAnswerMemory:
    async def finalize(
        self,
        *,
        user_text: str,
        final_answer: str,
        selected_model: SelectedModel,
        remaining_model_calls: int,
        workspace_key: str,
        remaining_provider_retries: int = 6,
    ) -> MemoryFinalizationResult:
        del (
            user_text,
            final_answer,
            selected_model,
            remaining_model_calls,
            remaining_provider_retries,
            workspace_key,
        )
        return MemoryFinalizationResult(enabled=False, status="disabled")


class CloudPostAnswerMemory:
    def __init__(
        self,
        *,
        distiller: MemoryDistillation,
        adapter: Mem0CloudAdapter,
        identity: Mem0Identity,
    ) -> None:
        self._distiller = distiller
        self._adapter = adapter
        self._identity = identity

    async def finalize(
        self,
        *,
        user_text: str,
        final_answer: str,
        selected_model: SelectedModel,
        remaining_model_calls: int,
        workspace_key: str,
        remaining_provider_retries: int = 6,
    ) -> MemoryFinalizationResult:
        if workspace_key != self._identity.workspace_key:
            return MemoryFinalizationResult(
                enabled=True,
                status="warning",
                diagnostics=(
                    Mem0Diagnostic(
                        code="mem0_scope_mismatch",
                        operation="finalize",
                    ),
                ),
            )
        distilled = await self._distiller.distill(
            user_text=user_text,
            final_answer=final_answer,
            selected_model=selected_model,
            remaining_model_calls=remaining_model_calls,
            remaining_provider_retries=remaining_provider_retries,
            workspace_key=workspace_key,
        )
        diagnostics: list[Mem0Diagnostic] = []
        if distilled.diagnostic is not None:
            diagnostics.append(distilled.diagnostic)
        if distilled.status is not DistillationStatus.COMPLETED:
            return MemoryFinalizationResult(
                enabled=True,
                status=distilled.status.value,
                usage=distilled.usage,
                model_calls=distilled.model_calls,
                diagnostics=tuple(diagnostics),
            )
        try:
            for candidate in distilled.candidates:
                workspace = (
                    workspace_key if candidate.scope.value == "workspace" else None
                )
                if await self._adapter.has_fact_hash(
                    candidate.fact_hash,
                    user_id=self._identity.user_id,
                    scope=candidate.scope,
                    workspace_key=workspace,
                ):
                    continue
                outcome = await self._adapter.add(candidate, self._identity)
                if not outcome.accepted and outcome.diagnostic is not None:
                    diagnostics.append(outcome.diagnostic)
        except Mem0CloudError as error:
            diagnostics.append(error.diagnostic)
        except Exception:
            diagnostics.append(
                Mem0Diagnostic(code="mem0_unavailable", operation="finalize")
            )
        return MemoryFinalizationResult(
            enabled=True,
            status="warning" if diagnostics else "completed",
            usage=distilled.usage,
            model_calls=distilled.model_calls,
            diagnostics=tuple(diagnostics),
        )


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
    current_user_text: str = ""
    compressor: AgentContextCompressor = field(
        default_factory=DisabledAgentContextCompressor
    )
    post_answer_memory: PostAnswerMemory = field(
        default_factory=DisabledPostAnswerMemory
    )
