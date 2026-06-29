from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Sequence

from awesome_agent.modeling import (
    AssistantMessage,
    ModelRequest,
    ModelStreamEvent,
    ModelTurn,
    StopReason,
    StructuredModelProvider,
    TextDelta,
    TurnCompleted,
)


class FakeModelProvider(StructuredModelProvider):
    def __init__(self, responses: Sequence[str | ModelTurn]) -> None:
        self._responses = deque(responses)
        self.requests: list[ModelRequest] = []

    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        self.requests.append(request)
        response = self._responses.popleft()
        if isinstance(response, ModelTurn):
            yield TurnCompleted(turn=response)
            return
        text = response
        yield TextDelta(text=text)
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content=text),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            )
        )
