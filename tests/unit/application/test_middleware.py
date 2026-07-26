from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Never

import pytest
from pydantic import ValidationError

from awesome_agent.application import middleware as middleware_module
from awesome_agent.application.command_results import (
    CommandError,
    UsageCommandPayload,
    result,
)
from awesome_agent.application.contracts import (
    ApplicationResult,
    ProductError,
    ProductErrorCode,
)
from awesome_agent.application.middleware import (
    ApplicationInvocation,
    ApplicationObservation,
    ApplicationOperation,
    ObservationalMiddleware,
    compose_middleware,
)
from awesome_agent.conversation import UsageSummary

_SESSION_ID = f"session_{'a' * 32}"
_CORRELATION_ID = f"correlation_{'b' * 32}"
_NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


class DiagnosticFailure(BaseException):
    pass


def _middleware(
    observations: list[ApplicationObservation],
    *,
    correlation_id: Callable[[], str] | None = None,
    monotonic: Callable[[], float] | None = None,
    clock: Callable[[], datetime] | None = None,
    sink: Callable[[ApplicationObservation], None] | None = None,
) -> ObservationalMiddleware:
    return ObservationalMiddleware(
        session_id=_SESSION_ID,
        correlation_id=correlation_id or (lambda: _CORRELATION_ID),
        monotonic=monotonic or iter((1.0, 1.25)).__next__,
        clock=clock or (lambda: _NOW),
        sink=sink or observations.append,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "product_error"])
async def test_observation_calls_next_once_and_preserves_exact_result(
    outcome: str,
) -> None:
    calls = 0
    result_value: object = (
        object()
        if outcome == "success"
        else ApplicationResult[object].failure(
            ProductError(
                code=ProductErrorCode.INVALID_ARGUMENTS,
                message="private error https://private.example/path",
                data={"state_directory": "C:\\private\\workspace"},
            )
        )
    )
    observations: list[ApplicationObservation] = []

    async def terminal(invocation: ApplicationInvocation) -> object:
        nonlocal calls
        calls += 1
        assert invocation.operation is ApplicationOperation.SUBMIT_TURN
        return result_value

    returned = await compose_middleware(
        (_middleware(observations),),
        terminal,
    )(ApplicationInvocation(operation=ApplicationOperation.SUBMIT_TURN))

    assert returned is result_value
    assert calls == 1
    observation = observations[0]
    assert tuple(ApplicationObservation.model_fields) == (
        "version",
        "timestamp",
        "session_id",
        "correlation_id",
        "operation",
        "outcome",
        "duration_ms",
        "error_code",
        "usage",
    )
    assert observation.correlation_id == _CORRELATION_ID
    assert observation.outcome == outcome
    assert observation.duration_ms == 250
    encoded = observation.model_dump_json()
    assert "private.example" not in encoded
    assert "private\\workspace" not in encoded
    assert "private error" not in encoded
    assert observation.error_code == (
        ProductErrorCode.INVALID_ARGUMENTS if outcome == "product_error" else None
    )


def test_invocation_rejects_every_non_allowlisted_field() -> None:
    with pytest.raises(ValidationError):
        ApplicationInvocation.model_validate(
            {
                "operation": "turn.submit",
                "payload": {
                    "prompt": "private prompt",
                    "url": "https://private.example",
                },
            }
        )


@pytest.mark.asyncio
async def test_observation_extracts_only_typed_usage() -> None:
    observations: list[ApplicationObservation] = []
    outcome = ApplicationResult.success(
        result(
            UsageCommandPayload(
                usage=UsageSummary(
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=2,
                    cache_read_tokens=3,
                    cache_write_tokens=1,
                    model_calls=2,
                    tool_calls=4,
                    provider_retries=1,
                    compressions=1,
                    active_execution_seconds=1.234,
                )
            )
        )
    )

    async def terminal(_invocation: ApplicationInvocation) -> object:
        return outcome

    await compose_middleware((_middleware(observations),), terminal)(
        ApplicationInvocation(operation=ApplicationOperation.EXECUTE_COMMAND)
    )

    usage = observations[0].usage
    assert usage is not None
    assert usage.model_dump() == {
        "input_tokens": 10,
        "output_tokens": 5,
        "reasoning_tokens": 2,
        "cache_read_tokens": 3,
        "cache_write_tokens": 1,
        "model_calls": 2,
        "tool_calls": 4,
        "provider_retries": 1,
        "compressions": 1,
        "active_execution_ms": 1234,
    }


@pytest.mark.asyncio
async def test_invalid_command_error_code_is_not_persisted() -> None:
    observations: list[ApplicationObservation] = []
    command_error = CommandError(
        code="private URL https://private.example",
        message="private body",
    )

    async def terminal(_invocation: ApplicationInvocation) -> object:
        return ApplicationResult.success(command_error)

    returned = await compose_middleware((_middleware(observations),), terminal)(
        ApplicationInvocation(operation=ApplicationOperation.EXECUTE_COMMAND)
    )

    assert returned == ApplicationResult.success(command_error)
    assert observations[0].outcome == "product_error"
    assert observations[0].error_code == "command_error"
    assert "private" not in observations[0].model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_observation_preserves_exact_exception_or_cancellation(
    cancelled: bool,
) -> None:
    observations: list[ApplicationObservation] = []
    failure: BaseException = (
        asyncio.CancelledError("cancel-private")
        if cancelled
        else RuntimeError("private exception detail")
    )

    async def terminal(_invocation: ApplicationInvocation) -> object:
        raise failure

    with pytest.raises(type(failure)) as raised:
        await compose_middleware((_middleware(observations),), terminal)(
            ApplicationInvocation(operation=ApplicationOperation.GET_STATE)
        )

    assert raised.value is failure
    assert observations[0].outcome == ("cancelled" if cancelled else "exception")
    assert observations[0].error_code == (None if cancelled else "internal_error")
    assert "private" not in observations[0].model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["correlation", "started"])
async def test_diagnostic_preparation_failure_does_not_skip_real_call(
    failure_point: str,
) -> None:
    calls = 0
    observations: list[ApplicationObservation] = []

    def fail() -> Never:
        raise DiagnosticFailure

    async def terminal(_invocation: ApplicationInvocation) -> object:
        nonlocal calls
        calls += 1
        return "exact-result"

    middleware = _middleware(
        observations,
        correlation_id=(fail if failure_point == "correlation" else None),
        monotonic=(fail if failure_point == "started" else None),
    )

    returned = await compose_middleware((middleware,), terminal)(
        ApplicationInvocation(operation=ApplicationOperation.GET_STATE)
    )

    assert returned == "exact-result"
    assert calls == 1
    assert observations == []


@pytest.mark.asyncio
async def test_result_fact_failure_preserves_exact_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations: list[ApplicationObservation] = []
    exact = object()

    def fail(_result: object) -> Never:
        raise DiagnosticFailure

    monkeypatch.setattr(middleware_module, "_result_facts", fail)

    async def terminal(_invocation: ApplicationInvocation) -> object:
        return exact

    returned = await compose_middleware((_middleware(observations),), terminal)(
        ApplicationInvocation(operation=ApplicationOperation.GET_STATE)
    )

    assert returned is exact
    assert observations == []


@pytest.mark.asyncio
@pytest.mark.parametrize("call_outcome", ["success", "exception", "cancelled"])
@pytest.mark.parametrize("diagnostic_failure", ["elapsed", "clock", "sink"])
async def test_emit_failure_never_replaces_call_outcome(
    call_outcome: str,
    diagnostic_failure: str,
) -> None:
    observations: list[ApplicationObservation] = []
    exact_result = object()
    exact_failure: BaseException | None = None
    if call_outcome == "exception":
        exact_failure = RuntimeError("private failure")
    elif call_outcome == "cancelled":
        exact_failure = asyncio.CancelledError("private cancellation")

    async def terminal(_invocation: ApplicationInvocation) -> object:
        if exact_failure is not None:
            raise exact_failure
        return exact_result

    def fail() -> Never:
        raise DiagnosticFailure

    def fail_sink(_observation: ApplicationObservation) -> Never:
        raise DiagnosticFailure

    monotonic: Callable[[], float] = (
        iter((1.0,)).__next__
        if diagnostic_failure == "elapsed"
        else iter((1.0, 1.1)).__next__
    )
    middleware = _middleware(
        observations,
        monotonic=monotonic,
        clock=(fail if diagnostic_failure == "clock" else None),
        sink=(fail_sink if diagnostic_failure == "sink" else None),
    )

    if exact_failure is None:
        returned = await compose_middleware((middleware,), terminal)(
            ApplicationInvocation(operation=ApplicationOperation.GET_STATE)
        )
        assert returned is exact_result
    else:
        with pytest.raises(type(exact_failure)) as raised:
            await compose_middleware((middleware,), terminal)(
                ApplicationInvocation(operation=ApplicationOperation.GET_STATE)
            )
        assert raised.value is exact_failure
    assert observations == []


@pytest.mark.asyncio
async def test_negative_elapsed_time_is_clamped() -> None:
    observations: list[ApplicationObservation] = []

    async def terminal(_invocation: ApplicationInvocation) -> object:
        return object()

    await compose_middleware(
        (_middleware(observations, monotonic=iter((2.0, 1.0)).__next__),),
        terminal,
    )(ApplicationInvocation(operation=ApplicationOperation.GET_STATE))

    assert observations[0].duration_ms == 0
