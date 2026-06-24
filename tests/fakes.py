from collections import deque

from awesome_agent.providers.base import ModelRequest, ModelResult


class FakeModelProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = deque(responses)
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResult:
        self.requests.append(request)
        return ModelResult(
            text=self._responses.popleft(),
            model="fake-model",
            provider="fake",
        )
