from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from awesome_agent.modeling import (
    AssistantMessage,
    GatewayEvent,
    ModelErrorCode,
    ModelErrorInfo,
    ModelGateway,
    ModelRequest,
    ModelTurn,
    ProviderProtocolError,
    ProviderRetrying,
    ReasoningDelta,
    ReasoningStarted,
    RetryPolicy,
    SelectedModel,
    StopReason,
    TextDelta,
    ToolArgumentsDelta,
    ToolCallStarted,
    TurnCompleted,
    TurnFailed,
    UserMessage,
)


def _request() -> ModelRequest:
    return ModelRequest(
        messages=(UserMessage(content="inspect"),),
        thinking_enabled=False,
    )


def _turn() -> ModelTurn:
    return ModelTurn(
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        assistant=AssistantMessage(content="done"),
        stop_reason=StopReason.COMPLETED,
    )


def _failure(code: ModelErrorCode) -> TurnFailed:
    return TurnFailed(
        error=ModelErrorInfo(
            code=code,
            message=f"safe {code.value}",
            retryable=code
            in {
                ModelErrorCode.CONNECTION,
                ModelErrorCode.TIMEOUT,
                ModelErrorCode.RATE_LIMIT,
                ModelErrorCode.TRANSIENT,
            },
            provider="deepseek",
            status_code=(429 if code is ModelErrorCode.RATE_LIMIT else None),
        )
    )


class ScriptedProvider:
    provider_id = "deepseek"

    def __init__(self, attempts: tuple[tuple[GatewayEvent, ...], ...]) -> None:
        self._attempts = list(attempts)
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[GatewayEvent]:
        self.requests.append(request)
        for event in self._attempts.pop(0):
            yield event


class CancellingProvider:
    provider_id = "deepseek"

    async def stream(self, request: ModelRequest) -> AsyncIterator[GatewayEvent]:
        del request
        raise asyncio.CancelledError
        yield TextDelta(text="unreachable")


async def _collect(gateway: ModelGateway, request: ModelRequest) -> list[GatewayEvent]:
    return [
        event
        async for event in gateway.stream(
            SelectedModel(
                provider="deepseek",
                model="deepseek/deepseek-v4-flash",
            ),
            request,
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        ModelErrorCode.CONNECTION,
        ModelErrorCode.TIMEOUT,
        ModelErrorCode.RATE_LIMIT,
        ModelErrorCode.TRANSIENT,
    ],
)
async def test_retryable_failure_before_output_retries_same_provider(
    code: ModelErrorCode,
) -> None:
    provider = ScriptedProvider(
        (
            (_failure(code),),
            (TextDelta(text="done"), TurnCompleted(turn=_turn())),
        )
    )
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    gateway = ModelGateway(
        {"deepseek": provider},
        retry_policy=RetryPolicy(max_retries=2, base_delay_seconds=0.5),
        sleeper=sleep,
    )
    request = _request()

    events = await _collect(gateway, request)

    assert [event.type for event in events] == [
        "provider.retrying",
        "text.delta",
        "turn.completed",
    ]
    retry = events[0]
    assert isinstance(retry, ProviderRetrying)
    assert retry.attempt == 2
    assert retry.maximum == 3
    assert retry.delay_seconds == 0.5
    assert retry.error_code is code
    assert delays == [0.5]
    assert provider.requests == [request, request]
    assert provider.requests[0] is provider.requests[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "visible",
    [
        TextDelta(text="visible"),
        ReasoningStarted(),
        ReasoningDelta(text="visible"),
        ToolCallStarted(index=0, call_id="call_1", name="read_file"),
        ToolArgumentsDelta(index=0, text="{"),
    ],
)
async def test_visible_output_permanently_disables_retry(visible: GatewayEvent) -> None:
    provider = ScriptedProvider(((visible, _failure(ModelErrorCode.TIMEOUT)),))
    gateway = ModelGateway(
        {"deepseek": provider},
        retry_policy=RetryPolicy(max_retries=6, base_delay_seconds=0),
        sleeper=_no_sleep,
    )

    events = await _collect(gateway, _request())

    assert events == [visible, _failure(ModelErrorCode.TIMEOUT)]
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_non_retryable_error_never_retries() -> None:
    provider = ScriptedProvider(((_failure(ModelErrorCode.AUTHENTICATION),),))
    gateway = ModelGateway(
        {"deepseek": provider},
        retry_policy=RetryPolicy(max_retries=6),
        sleeper=_no_sleep,
    )

    events = await _collect(gateway, _request())

    assert len(events) == 1
    assert isinstance(events[0], TurnFailed)
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_retry_exhaustion_uses_capped_exponential_backoff() -> None:
    failure = _failure(ModelErrorCode.CONNECTION)
    provider = ScriptedProvider(((failure,), (failure,), (failure,)))
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    gateway = ModelGateway(
        {"deepseek": provider},
        retry_policy=RetryPolicy(
            max_retries=2,
            base_delay_seconds=2,
            max_delay_seconds=3,
        ),
        sleeper=sleep,
    )

    events = await _collect(gateway, _request())

    assert [event.type for event in events] == [
        "provider.retrying",
        "provider.retrying",
        "turn.failed",
    ]
    assert delays == [2, 3]
    assert len(provider.requests) == 3


@pytest.mark.asyncio
async def test_complete_returns_turn_with_retry_count_in_usage() -> None:
    provider = ScriptedProvider(
        (
            (_failure(ModelErrorCode.RATE_LIMIT),),
            (TurnCompleted(turn=_turn()),),
        )
    )
    gateway = ModelGateway(
        {"deepseek": provider},
        retry_policy=RetryPolicy(max_retries=2, base_delay_seconds=0),
        sleeper=_no_sleep,
    )

    turn = await gateway.complete(
        SelectedModel(provider="deepseek", model="deepseek/deepseek-v4-flash"),
        _request(),
    )

    assert turn.assistant.content == "done"
    assert turn.usage.provider_retries == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attempt",
    [(), (TurnCompleted(turn=_turn()), TurnCompleted(turn=_turn()))],
)
async def test_complete_requires_exactly_one_terminal_turn(
    attempt: tuple[GatewayEvent, ...],
) -> None:
    gateway = ModelGateway(
        {"deepseek": ScriptedProvider((attempt,))},
        retry_policy=RetryPolicy(max_retries=0),
        sleeper=_no_sleep,
    )

    with pytest.raises(ProviderProtocolError):
        await gateway.complete(
            SelectedModel(
                provider="deepseek",
                model="deepseek/deepseek-v4-flash",
            ),
            _request(),
        )


@pytest.mark.asyncio
async def test_cancellation_propagates_without_retry() -> None:
    gateway = ModelGateway(
        {"deepseek": CancellingProvider()},
        retry_policy=RetryPolicy(max_retries=6),
        sleeper=_no_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        await _collect(gateway, _request())


async def _no_sleep(delay: float) -> None:
    del delay
