from typing import Any, Literal, cast

from openai import AsyncOpenAI

from awesome_agent.providers.base import (
    ModelProvider,
    ModelRequest,
    ModelResult,
    ModelUsage,
)


class DeepSeekProvider(ModelProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.deepseek.com",
        thinking_enabled: bool = True,
        reasoning_effort: Literal["high", "max"] = "high",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._thinking_enabled = thinking_enabled
        self._reasoning_effort = reasoning_effort
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )

    async def generate(self, request: ModelRequest) -> ModelResult:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            max_tokens=request.max_output_tokens,
            reasoning_effort=cast(Any, self._reasoning_effort),
            extra_body={
                "thinking": {
                    "type": "enabled" if self._thinking_enabled else "disabled"
                }
            },
        )
        message = response.choices[0].message
        usage = response.usage
        return ModelResult(
            text=message.content or "",
            model=self._model,
            provider="deepseek",
            response_id=response.id,
            usage=ModelUsage(
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
            ),
        )
