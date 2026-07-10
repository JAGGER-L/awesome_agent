from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from awesome_agent.modeling.stream import ModelStreamEvent
from awesome_agent.modeling.turns import ModelRequest, ProviderId


class ModelProvider(Protocol):
    provider_id: ProviderId

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]: ...
