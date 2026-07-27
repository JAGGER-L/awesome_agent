from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import cast

import pytest

from awesome_agent.application.runtime_resources import RuntimeResources
from awesome_agent.modeling import GatewayFactory, ModelGateway


class _BorrowedGateway:
    def __init__(self) -> None:
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


def test_generations_are_independent_and_gateways_are_cached_by_selection() -> None:
    first = RuntimeResources()
    second = RuntimeResources()
    borrowed: dict[tuple[str, str], _BorrowedGateway] = {}
    calls: list[tuple[str, str]] = []

    def build(provider: str, model: str) -> ModelGateway:
        key = (provider, model)
        calls.append(key)
        gateway = _BorrowedGateway()
        borrowed[key] = gateway
        return cast(ModelGateway, gateway)

    factory = first.bind_gateway_factory(cast(GatewayFactory, build))

    assert first.generation is not second.generation
    first_deepseek = factory("deepseek", "deepseek/deepseek-v4-flash")
    assert factory("deepseek", "deepseek/deepseek-v4-flash") is first_deepseek
    assert factory("deepseek", "deepseek/deepseek-v4-pro") is not first_deepseek
    assert factory("kimi", "kimi/kimi-k2.6") is not first_deepseek
    assert calls == [
        ("deepseek", "deepseek/deepseek-v4-flash"),
        ("deepseek", "deepseek/deepseek-v4-pro"),
        ("kimi", "kimi/kimi-k2.6"),
    ]


def test_gateway_factory_failure_is_not_cached() -> None:
    resources = RuntimeResources()
    calls = 0

    def fail(_: str, __: str) -> ModelGateway:
        nonlocal calls
        calls += 1
        raise RuntimeError("factory failed")

    factory = resources.bind_gateway_factory(cast(GatewayFactory, fail))

    for _ in range(2):
        with pytest.raises(RuntimeError, match="factory failed"):
            factory("deepseek", "deepseek/deepseek-v4-flash")
    assert calls == 2


@pytest.mark.asyncio
async def test_reader_drain_closes_owned_resources_once_in_reverse_order() -> None:
    resources = RuntimeResources()
    closed: list[str] = []

    async def record(name: object) -> object:
        closed.append(cast(str, name))
        return None

    resources.push_async_callback(record, "provider")
    resources.push_async_callback(record, "mem0")
    resources.push_async_callback(record, "mcp")

    with resources.reader():
        first = asyncio.create_task(resources.aclose())
        second = asyncio.create_task(resources.aclose())
        await asyncio.sleep(0)
        assert resources.reader_count == 1
        assert closed == []

    await asyncio.gather(first, second)
    await resources.aclose()

    assert resources.closed is True
    assert closed == ["mcp", "mem0", "provider"]


@pytest.mark.asyncio
async def test_close_failure_runs_remaining_callbacks_and_is_stable() -> None:
    resources = RuntimeResources()
    closed: list[str] = []
    failure = RuntimeError("mem0 close failed")

    async def record(name: object) -> object:
        closed.append(cast(str, name))
        return None

    async def fail() -> object:
        closed.append("mem0")
        raise failure

    resources.push_async_callback(record, "provider")
    resources.push_async_callback(fail)
    resources.push_async_callback(record, "mcp")

    for _ in range(2):
        with pytest.raises(RuntimeError) as raised:
            await resources.aclose()
        assert raised.value is failure

    assert closed == ["mcp", "mem0", "provider"]


@pytest.mark.asyncio
async def test_close_timeout_cancels_hung_callback_and_runs_remaining_callbacks() -> (
    None
):
    resources = RuntimeResources(close_timeout_seconds=0.01)
    closed: list[str] = []
    hanging_cancelled = False

    async def record(name: object) -> object:
        closed.append(cast(str, name))
        return None

    async def hang() -> None:
        nonlocal hanging_cancelled
        closed.append("mcp")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            hanging_cancelled = True
            raise

    resources.push_async_callback(record, "provider")
    resources.push_async_callback(record, "mem0")
    resources.push_async_callback(hang)

    for _ in range(2):
        with pytest.raises(TimeoutError):
            await resources.aclose()

    assert hanging_cancelled is True
    assert closed == ["mcp", "mem0", "provider"]


@pytest.mark.asyncio
async def test_close_uses_clean_context_and_does_not_close_borrowed_gateway() -> None:
    request_runtime: ContextVar[str | None] = ContextVar(
        "test_request_runtime",
        default=None,
    )
    resources = RuntimeResources()
    borrowed = _BorrowedGateway()
    observed: list[str | None] = []

    def build(_: str, __: str) -> ModelGateway:
        return cast(ModelGateway, borrowed)

    async def observe_context() -> object:
        observed.append(request_runtime.get())
        return None

    resources.bind_gateway_factory(cast(GatewayFactory, build))(
        "kimi",
        "kimi/kimi-k2.6",
    )
    resources.push_async_callback(observe_context)
    token = request_runtime.set("old-runtime")
    try:
        await resources.aclose()
    finally:
        request_runtime.reset(token)

    assert observed == [None]
    assert borrowed.close_calls == 0


@pytest.mark.asyncio
async def test_closing_generation_rejects_new_readers_and_gateways() -> None:
    resources = RuntimeResources()
    resources.bind_gateway_factory(
        cast(GatewayFactory, lambda _provider, _model: cast(ModelGateway, object()))
    )
    await resources.aclose()

    with pytest.raises(RuntimeError, match="closing or closed"), resources.reader():
        pass
    with pytest.raises(RuntimeError, match="closing or closed"):
        resources.gateway("deepseek", "deepseek/deepseek-v4-flash")
