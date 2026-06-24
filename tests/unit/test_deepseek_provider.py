from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from openai import AsyncOpenAI

from awesome_agent.providers.base import ModelRequest
from awesome_agent.providers.deepseek import DeepSeekProvider


@pytest.mark.asyncio
async def test_deepseek_provider_uses_chat_completions_and_thinking() -> None:
    response = SimpleNamespace(
        id="chatcmpl_123",
        choices=[SimpleNamespace(message=SimpleNamespace(content="result"))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
    )
    create = AsyncMock(return_value=response)
    client = cast(
        AsyncOpenAI,
        cast(
            Any,
            SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            ),
        ),
    )
    provider = DeepSeekProvider(
        api_key="test",
        model="deepseek-v4-pro",
        client=client,
    )

    result = await provider.generate(
        ModelRequest(system_prompt="system", user_prompt="user")
    )

    assert result.text == "result"
    assert result.provider == "deepseek"
    assert result.usage.input_tokens == 11
    awaited = create.await_args
    assert awaited is not None
    call = awaited.kwargs
    assert call["model"] == "deepseek-v4-pro"
    assert call["extra_body"] == {"thinking": {"type": "enabled"}}
    assert call["reasoning_effort"] == "high"
