from typing import Any, cast

from openai import AsyncOpenAI

from awesome_agent.providers.base import (
    ModelProvider,
    ModelRequest,
    ModelResult,
    ModelUsage,
)


class OpenAIProvider(ModelProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._client = client or AsyncOpenAI(api_key=api_key)

    async def generate(self, request: ModelRequest) -> ModelResult:
        response = await self._client.responses.create(
            model=cast(Any, self._model),
            instructions=request.system_prompt,
            input=request.user_prompt,
            max_output_tokens=request.max_output_tokens,
            store=False,
        )
        usage = response.usage
        return ModelResult(
            text=response.output_text,
            model=self._model,
            provider="openai",
            response_id=response.id,
            usage=ModelUsage(
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
            ),
        )
