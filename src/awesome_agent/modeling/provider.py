from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Protocol

from awesome_agent.modeling.errors import ProviderProtocolError, error_from_info
from awesome_agent.modeling.stream import ModelStreamEvent, TurnCompleted, TurnFailed
from awesome_agent.modeling.turns import ModelRequest, ModelTurn


class ModelProvider(Protocol):
    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        """Stream one structured model turn."""
        ...

    async def complete(self, request: ModelRequest) -> ModelTurn:
        """Collect one structured model turn."""
        ...


class StructuredModelProvider(ABC):
    @abstractmethod
    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        raise NotImplementedError

    async def complete(self, request: ModelRequest) -> ModelTurn:
        completed: ModelTurn | None = None
        async for event in self.stream(request):
            if isinstance(event, TurnFailed):
                raise error_from_info(event.error)
            if isinstance(event, TurnCompleted):
                completed = event.turn
        if completed is None:
            raise ProviderProtocolError(
                "Provider stream ended without a completed turn.",
                provider="unknown",
            )
        return completed
