from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from openai import AsyncOpenAI

from awesome_agent.providers.base import ModelRequest
from awesome_agent.providers.openai import OpenAIProvider


@pytest.mark.asyncio
async def test_openai_provider_maps_response() -> None:
    response = SimpleNamespace(
        id="resp_123",
        output_text="result",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    create = AsyncMock(return_value=response)
    client = cast(
        AsyncOpenAI,
        cast(Any, SimpleNamespace(responses=SimpleNamespace(create=create))),
    )
    provider = OpenAIProvider(api_key="test", model="test-model", client=client)

    result = await provider.generate(
        ModelRequest(system_prompt="system", user_prompt="user")
    )

    assert result.text == "result"
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
    create.assert_awaited_once()
