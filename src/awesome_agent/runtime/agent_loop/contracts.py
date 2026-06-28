from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
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
    BEFORE_MODEL = "before_model"
    WRAP_MODEL_CALL = "wrap_model_call"
    AFTER_MODEL = "after_model"
    WRAP_TOOL_CALL = "wrap_tool_call"
    AFTER_AGENT = "after_agent"


@dataclass(slots=True)
class MiddlewareContext:
    run_id: str
    agent_id: str
    runtime_route: str
    messages: list[ModelMessage]
    metadata: dict[str, Any] = field(default_factory=dict)


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
