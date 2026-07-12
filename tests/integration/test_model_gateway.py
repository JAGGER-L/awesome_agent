from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr

from awesome_agent.config import (
    ApplicationConfig,
    BudgetConfig,
    KimiRegion,
    MemoryConfig,
    ProviderConfig,
    SecretStatus,
)
from awesome_agent.config.loader import SecretValues
from awesome_agent.modeling import (
    ModelGateway,
    ModelRequest,
    RetryPolicy,
    SelectedModel,
    UserMessage,
)
from awesome_agent.providers import create_provider_mapping


class AsyncEvents:
    def __init__(self, text: str) -> None:
        self._text = text

    async def __aiter__(self) -> AsyncIterator[object]:
        yield SimpleNamespace(
            id=f"response_{self._text}",
            choices=(
                SimpleNamespace(
                    finish_reason="stop",
                    delta=SimpleNamespace(
                        reasoning_content=None,
                        content=self._text,
                        tool_calls=(),
                    ),
                ),
            ),
            usage=SimpleNamespace(
                prompt_tokens=3,
                completion_tokens=2,
                completion_tokens_details=None,
                prompt_tokens_details=None,
            ),
        )


def _client(create: AsyncMock) -> AsyncOpenAI:
    return cast(
        AsyncOpenAI,
        cast(
            Any,
            SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            ),
        ),
    )


async def _no_sleep(delay: float) -> None:
    del delay


@pytest.mark.asyncio
async def test_both_configured_providers_share_one_networkless_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = ApplicationConfig(
        providers=ProviderConfig(kimi_region=KimiRegion.GLOBAL),
        budgets=BudgetConfig(),
        memory=MemoryConfig(),
        secret_status=SecretStatus(
            deepseek_api_key=True,
            moonshot_api_key=True,
        ),
    )
    secrets = SecretValues(
        deepseek_api_key=SecretStr("deepseek-original"),
        moonshot_api_key=SecretStr("kimi-original"),
    )
    deepseek_create = AsyncMock(return_value=AsyncEvents("deepseek"))
    kimi_create = AsyncMock(return_value=AsyncEvents("kimi"))
    providers = create_provider_mapping(
        application,
        secrets,
        models={
            "deepseek": "deepseek/deepseek-v4-pro",
            "kimi": "kimi/kimi-k2.5",
        },
        deepseek_client=_client(deepseek_create),
        kimi_client=_client(kimi_create),
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "changed-after-composition")
    monkeypatch.setenv("MOONSHOT_API_KEY", "changed-after-composition")
    gateway = ModelGateway(
        providers,
        retry_policy=RetryPolicy(max_retries=0),
        sleeper=_no_sleep,
    )
    request = ModelRequest(
        messages=(UserMessage(content="hello"),),
        thinking_enabled=False,
    )

    deepseek_turn = await gateway.complete(
        SelectedModel(provider="deepseek", model="deepseek/deepseek-v4-pro"),
        request,
    )
    kimi_turn = await gateway.complete(
        SelectedModel(provider="kimi", model="kimi/kimi-k2.5"),
        request,
    )

    assert tuple(providers) == ("deepseek", "kimi")
    assert deepseek_turn.assistant.content == "deepseek"
    assert kimi_turn.assistant.content == "kimi"
    deepseek_call = deepseek_create.await_args
    kimi_call = kimi_create.await_args
    assert deepseek_call is not None
    assert kimi_call is not None
    assert deepseek_call.kwargs["model"] == "deepseek-v4-pro"
    assert kimi_call.kwargs["model"] == "kimi-k2.5"
