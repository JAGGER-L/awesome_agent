from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from awesome_agent.modeling import ModelMessage


class AgentLoopStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING = "waiting"
    CANCELLED = "cancelled"
    RECOVERY_REQUIRED = "recovery_required"


class MiddlewareStage(StrEnum):
    BEFORE_AGENT = "before_agent"
    BEFORE_TOOL_EXPOSURE = "before_tool_exposure"
    AFTER_TOOL_EXPOSURE = "after_tool_exposure"
    BEFORE_MODEL = "before_model"
    WRAP_MODEL_CALL = "wrap_model_call"
    AFTER_MODEL = "after_model"
    WRAP_TOOL_CALL = "wrap_tool_call"
    AFTER_AGENT = "after_agent"


@dataclass(frozen=True, slots=True)
class TraceContext:
    run_id: str
    parent_run_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    runtime_route: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityContext:
    subject_id: str
    subject_kind: str
    policy_id: str | None
    allowed_tool_names: tuple[str, ...]
    denied_tool_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AssignmentContext:
    assignment_id: str | None
    leader_run_id: str | None
    role: str | None
    objective: str | None


@dataclass(frozen=True, slots=True)
class TokenBudgetContext:
    token_limit: int | None
    input_tokens_used: int = 0
    output_tokens_used: int = 0
    reasoning_tokens_used: int = 0


@dataclass(frozen=True, slots=True)
class HandoffContext:
    handoff_id: str | None
    source_agent: str | None
    target_agent: str | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class ErrorClassificationContext:
    category: str | None
    retryable: bool | None
    origin: str | None


@dataclass(slots=True)
class MiddlewareContext:
    run_id: str
    agent_id: str
    runtime_route: str
    messages: list[ModelMessage]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    trace: TraceContext | None = None
    capabilities: CapabilityContext | None = None
    assignment: AssignmentContext | None = None
    budget: TokenBudgetContext | None = None
    handoff: HandoffContext | None = None
    error: ErrorClassificationContext | None = None

    def __post_init__(self) -> None:
        self.metadata = MappingProxyType(dict(self.metadata))


@dataclass(frozen=True, slots=True)
class MiddlewareDecision:
    continue_loop: bool = True
    reason: str | None = None
    status: AgentLoopStatus | None = None

    @classmethod
    def continue_(cls) -> MiddlewareDecision:
        return cls()

    @classmethod
    def stop(
        cls,
        status: AgentLoopStatus,
        reason: str,
    ) -> MiddlewareDecision:
        return cls(continue_loop=False, status=status, reason=reason)


@dataclass(frozen=True, slots=True)
class AgentLoopResult:
    status: AgentLoopStatus
    messages: list[ModelMessage]
    final_answer: str | None = None
    reason: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
