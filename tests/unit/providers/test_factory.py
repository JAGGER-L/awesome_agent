from __future__ import annotations

from typing import Any, cast

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
    SecretValues,
)
from awesome_agent.providers import managed_gateway_factory


class _TrackingClient:
    def __init__(self, name: str, closed: list[str]) -> None:
        self.name = name
        self._closed = closed
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        self._closed.append(self.name)


class _FailingCloseClient(_TrackingClient):
    async def close(self) -> None:
        await super().close()
        raise RuntimeError("provider cleanup failed")


def _application() -> ApplicationConfig:
    return ApplicationConfig(
        providers=ProviderConfig(),
        budgets=BudgetConfig(provider_retries=1),
        memory=MemoryConfig(),
        secret_status=SecretStatus(
            deepseek_api_key=True,
            moonshot_api_key=True,
        ),
    )


def _secrets() -> SecretValues:
    return SecretValues(
        deepseek_api_key=SecretStr("deepseek-secret"),
        moonshot_api_key=SecretStr("kimi-secret"),
    )


@pytest.mark.asyncio
async def test_managed_factory_reuses_candidate_clients_and_closes_in_reverse() -> None:
    created: list[tuple[dict[str, object], _TrackingClient]] = []
    closed: list[str] = []

    def construct(**kwargs: object) -> AsyncOpenAI:
        name = "deepseek" if "deepseek" in str(kwargs["base_url"]) else "kimi"
        client = _TrackingClient(name, closed)
        created.append((dict(kwargs), client))
        return cast(AsyncOpenAI, cast(Any, client))

    async with managed_gateway_factory(
        _application(),
        _secrets(),
        client_factory=construct,
    ) as build:
        deepseek_flash = build("deepseek", "deepseek/deepseek-v4-flash")
        deepseek_pro = build("deepseek", "deepseek/deepseek-v4-pro")
        kimi = build("kimi", "kimi/kimi-k2.6")
        deepseek_clients = {
            cast(Any, provider)._client
            for gateway in (deepseek_flash, deepseek_pro)
            for provider in cast(Any, gateway)._providers.values()
        }
        kimi_client = next(iter(cast(Any, kimi)._providers.values()))._client

        assert len(deepseek_clients) == 1
        assert kimi_client not in deepseek_clients
        assert cast(Any, deepseek_flash)._retry_policy.max_retries == 1
        assert all(client.close_calls == 0 for _, client in created)

    assert [kwargs["timeout"] for kwargs, _ in created] == [60.0, 60.0]
    assert [str(kwargs["base_url"]) for kwargs, _ in created] == [
        "https://api.deepseek.com",
        "https://api.moonshot.cn/v1",
    ]
    assert closed == ["kimi", "deepseek"]
    assert all(client.close_calls == 1 for _, client in created)


@pytest.mark.asyncio
async def test_partial_provider_construction_failure_closes_first_client() -> None:
    closed: list[str] = []
    first = _TrackingClient("deepseek", closed)
    calls = 0

    def construct(**_: object) -> AsyncOpenAI:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second provider construction failed")
        return cast(AsyncOpenAI, cast(Any, first))

    with pytest.raises(RuntimeError, match="second provider construction failed"):
        async with managed_gateway_factory(
            _application(),
            _secrets(),
            client_factory=construct,
        ):
            raise AssertionError("factory must not be published")

    assert first.close_calls == 1
    assert closed == ["deepseek"]


@pytest.mark.asyncio
async def test_partial_construction_preserves_primary_when_cleanup_fails() -> None:
    closed: list[str] = []
    first = _FailingCloseClient("deepseek", closed)
    calls = 0

    def construct(**_: object) -> AsyncOpenAI:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("primary provider construction failure")
        return cast(AsyncOpenAI, cast(Any, first))

    with pytest.raises(ValueError, match="primary provider construction failure"):
        async with managed_gateway_factory(
            _application(),
            _secrets(),
            client_factory=construct,
        ):
            raise AssertionError("factory must not be published")

    assert first.close_calls == 1
    assert closed == ["deepseek"]


@pytest.mark.asyncio
async def test_unconfigured_candidate_creates_no_provider_clients() -> None:
    application = _application().model_copy(update={"secret_status": SecretStatus()})
    calls = 0

    def construct(**_: object) -> AsyncOpenAI:
        nonlocal calls
        calls += 1
        raise AssertionError("unconfigured candidate must not create a client")

    async with managed_gateway_factory(
        application,
        SecretValues(),
        client_factory=construct,
    ) as build:
        with pytest.raises(AssertionError, match="credential preflight"):
            build("deepseek", "deepseek/deepseek-v4-flash")

    assert calls == 0


@pytest.mark.asyncio
async def test_kimi_region_is_frozen_into_candidate_client() -> None:
    application = _application().model_copy(
        update={
            "providers": ProviderConfig(kimi_region=KimiRegion.GLOBAL),
            "secret_status": SecretStatus(moonshot_api_key=True),
        }
    )
    secrets = SecretValues(moonshot_api_key=SecretStr("kimi-secret"))
    captured: list[dict[str, object]] = []
    client = _TrackingClient("kimi", [])

    def construct(**kwargs: object) -> AsyncOpenAI:
        captured.append(dict(kwargs))
        return cast(AsyncOpenAI, cast(Any, client))

    async with managed_gateway_factory(
        application,
        secrets,
        client_factory=construct,
    ) as build:
        gateway = build("kimi", "kimi/kimi-k2.6")
        assert cast(Any, gateway)._retry_policy.max_retries == 1

    assert str(captured[0]["base_url"]) == "https://api.moonshot.ai/v1"
    assert client.close_calls == 1
