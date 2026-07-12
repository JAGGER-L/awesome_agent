from __future__ import annotations

import asyncio

import pytest

from awesome_agent.application.contracts import (
    ApplicationResult,
    ProductError,
    ProductErrorCode,
)
from awesome_agent.application.middleware import (
    ApplicationInvocation,
    ApplicationObservation,
    ObservationalMiddleware,
    compose_middleware,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "product_error"])
async def test_observation_calls_next_once_and_preserves_exact_result(
    outcome: str,
) -> None:
    calls = 0
    result: object = (
        object()
        if outcome == "success"
        else ApplicationResult[object].failure(
            ProductError(
                code=ProductErrorCode.INVALID_ARGUMENTS,
                message="invalid",
            )
        )
    )
    observations: list[ApplicationObservation] = []

    async def terminal(invocation: ApplicationInvocation) -> object:
        nonlocal calls
        calls += 1
        assert invocation.payload["content"] == "token=private-value"
        return result

    chain = compose_middleware(
        (
            ObservationalMiddleware(
                correlation_id=lambda: "correlation_1",
                monotonic=iter((1.0, 1.25)).__next__,
                sink=observations.append,
            ),
        ),
        terminal,
    )
    invocation = ApplicationInvocation(
        name="turn.submit",
        payload={"content": "token=private-value"},
    )

    returned = await chain(invocation)

    assert returned is result
    assert calls == 1
    assert observations[0].correlation_id == "correlation_1"
    assert observations[0].outcome == outcome
    assert observations[0].latency_seconds == 0.25
    assert "private-value" not in observations[0].model_dump_json()
    assert "[REDACTED:content]" in observations[0].model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_observation_preserves_exception_or_cancellation(cancelled: bool) -> None:
    calls = 0
    observations: list[ApplicationObservation] = []

    async def terminal(invocation: ApplicationInvocation) -> object:
        nonlocal calls
        del invocation
        calls += 1
        if cancelled:
            raise asyncio.CancelledError
        raise RuntimeError("private exception detail")

    chain = compose_middleware(
        (
            ObservationalMiddleware(
                correlation_id=lambda: "correlation_1",
                monotonic=iter((1.0, 1.1)).__next__,
                sink=observations.append,
            ),
        ),
        terminal,
    )

    expected = asyncio.CancelledError if cancelled else RuntimeError
    with pytest.raises(expected):
        await chain(ApplicationInvocation(name="operation", payload={}))

    assert calls == 1
    assert observations[0].outcome == ("cancelled" if cancelled else "exception")
    assert "private exception detail" not in observations[0].model_dump_json()
