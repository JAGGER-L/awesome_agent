from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, cast

from awesome_agent.modeling.provider import ModelProvider
from awesome_agent.modeling.stream import ModelStreamEvent
from awesome_agent.modeling.turns import ModelRequest


class ModelExecutionError(RuntimeError):
    """Base class for parent-owned model execution failures."""


class ModelExecutionTimeout(ModelExecutionError):
    def __init__(self, phase: str, timeout_seconds: float) -> None:
        super().__init__(
            f"Model stream timed out during {phase} after {timeout_seconds:.1f}s."
        )
        self.phase = phase
        self.timeout_seconds = timeout_seconds


class ModelExecutionProtocolError(ModelExecutionError):
    """Raised when a model execution child emits invalid protocol data."""


class ModelExecutionCancelled(ModelExecutionError):
    """Raised when the parent cancelled model execution."""


@dataclass(frozen=True, slots=True)
class ModelExecutionContext:
    run_id: str
    thread_id: str
    model: str | None
    provider: str | None


class ModelExecutionBackend(Protocol):
    def stream(
        self,
        request: ModelRequest,
        *,
        context: ModelExecutionContext,
    ) -> AsyncIterator[ModelStreamEvent]: ...


@dataclass(frozen=True, slots=True)
class ModelExecutionService:
    backend: ModelExecutionBackend

    def stream(
        self,
        request: ModelRequest,
        *,
        context: ModelExecutionContext,
    ) -> AsyncIterator[ModelStreamEvent]:
        return self.backend.stream(request, context=context)


@dataclass(frozen=True, slots=True)
class InProcessModelExecutionBackend:
    """Test/non-production backend that invokes provider SDKs in-process."""

    provider_factory: object

    def stream(
        self,
        request: ModelRequest,
        *,
        context: ModelExecutionContext,
    ) -> AsyncIterator[ModelStreamEvent]:
        provider = self._create_provider(context)
        return provider.stream(request)

    def _create_provider(self, context: ModelExecutionContext) -> ModelProvider:
        create = getattr(self.provider_factory, "create", None)
        if callable(create):
            return cast(ModelProvider, create(context.model or ""))
        if callable(self.provider_factory):
            return cast(ModelProvider, self.provider_factory(context.model or ""))
        raise TypeError("provider_factory must be callable or expose create(model).")
