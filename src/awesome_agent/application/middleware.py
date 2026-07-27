from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from awesome_agent.application.command_results import (
    CommandError,
    CommandResult,
    CompactCommandPayload,
    UsageCommandPayload,
)
from awesome_agent.application.contracts import ApplicationResult, ApplicationState
from awesome_agent.conversation import UsageSummary
from awesome_agent.core.contracts import MAX_JSON_SAFE_INTEGER, JsonSafeInteger

type ApplicationCall = Callable[["ApplicationInvocation"], Awaitable[object]]
type ObservationSink = Callable[["ApplicationObservation"], None]
type ObservationOutcome = Literal[
    "success",
    "product_error",
    "cancelled",
    "exception",
]


class ApplicationOperation(StrEnum):
    INITIALIZE = "initialize"
    GET_STATE = "application.getState"
    LIST_THREADS = "thread.list"
    SEARCH_THREADS = "thread.search"
    READ_THREAD = "thread.read"
    SUBMIT_TURN = "turn.submit"
    EXECUTE_DIRECT = "direct.execute"
    EXECUTE_COMMAND = "command.execute"
    SET_PROVIDER_CREDENTIAL = "provider.credential.set"
    RESPOND_INTERACTION = "interaction.respond"
    CANCEL_OPERATION = "operation.cancel"
    SHUTDOWN = "shutdown"


class ApplicationInvocation(BaseModel):
    """One surface call identified only by a closed, non-user-controlled name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: ApplicationOperation


class DiagnosticUsage(BaseModel):
    """The only Usage fields admitted to an Application diagnostic record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: JsonSafeInteger = Field(default=0, ge=0)
    output_tokens: JsonSafeInteger = Field(default=0, ge=0)
    reasoning_tokens: JsonSafeInteger = Field(default=0, ge=0)
    cache_read_tokens: JsonSafeInteger = Field(default=0, ge=0)
    cache_write_tokens: JsonSafeInteger = Field(default=0, ge=0)
    model_calls: JsonSafeInteger = Field(default=0, ge=0)
    tool_calls: JsonSafeInteger = Field(default=0, ge=0)
    provider_retries: JsonSafeInteger = Field(default=0, ge=0)
    compressions: JsonSafeInteger = Field(default=0, ge=0)
    web_requests: JsonSafeInteger = Field(default=0, ge=0)
    active_execution_ms: JsonSafeInteger = Field(default=0, ge=0)


class ApplicationObservation(BaseModel):
    """Versioned allowlist for one persisted Application diagnostic record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    timestamp: datetime
    session_id: str = Field(
        pattern=r"^session_[A-Za-z0-9]+$",
        max_length=128,
    )
    correlation_id: str = Field(
        pattern=r"^correlation_[A-Za-z0-9]+$",
        max_length=128,
    )
    operation: ApplicationOperation
    outcome: ObservationOutcome
    duration_ms: JsonSafeInteger = Field(ge=0)
    error_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,127}$",
    )
    usage: DiagnosticUsage | None = None

    @model_validator(mode="after")
    def validate_timestamp_and_error(self) -> ApplicationObservation:
        if self.timestamp.utcoffset() != timedelta(0):
            raise ValueError("Application observation timestamps must use UTC.")
        if self.outcome == "product_error" and self.error_code is None:
            raise ValueError("Product errors require a stable error code.")
        if self.outcome == "exception" and self.error_code != "internal_error":
            raise ValueError("Exceptions use the fixed internal_error code.")
        if self.outcome in {"success", "cancelled"} and self.error_code is not None:
            raise ValueError("Successful and cancelled observations omit error codes.")
        return self


class ApplicationMiddleware(Protocol):
    async def __call__(
        self,
        invocation: ApplicationInvocation,
        next_call: ApplicationCall,
    ) -> object: ...


class ObservationalMiddleware:
    """Observe bounded surface-call facts without changing call semantics."""

    def __init__(
        self,
        *,
        session_id: str,
        correlation_id: Callable[[], str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float],
        sink: ObservationSink,
    ) -> None:
        self._session_id = session_id
        self._correlation_id = correlation_id
        self._clock = clock
        self._monotonic = monotonic
        self._sink = sink

    async def __call__(
        self,
        invocation: ApplicationInvocation,
        next_call: ApplicationCall,
    ) -> object:
        prepared = self._prepare_observation()
        try:
            result = await next_call(invocation)
        except asyncio.CancelledError:
            if prepared is not None:
                self._observe(
                    invocation,
                    correlation_id=prepared[0],
                    started=prepared[1],
                    outcome="cancelled",
                )
            raise
        except Exception:
            if prepared is not None:
                self._observe(
                    invocation,
                    correlation_id=prepared[0],
                    started=prepared[1],
                    outcome="exception",
                    error_code="internal_error",
                )
            raise
        if prepared is not None:
            try:
                outcome, error_code, usage = _result_facts(result)
                self._observe(
                    invocation,
                    correlation_id=prepared[0],
                    started=prepared[1],
                    outcome=outcome,
                    error_code=error_code,
                    usage=usage,
                )
            except BaseException:
                pass
        return result

    def _prepare_observation(self) -> tuple[str, float] | None:
        try:
            return self._correlation_id(), self._monotonic()
        except BaseException:
            return None

    def _observe(
        self,
        invocation: ApplicationInvocation,
        *,
        correlation_id: str,
        started: float,
        outcome: ObservationOutcome,
        error_code: str | None = None,
        usage: DiagnosticUsage | None = None,
    ) -> None:
        try:
            elapsed = self._monotonic() - started
            duration_ms = round(max(0.0, elapsed) * 1_000)
            self._sink(
                ApplicationObservation(
                    timestamp=self._clock(),
                    session_id=self._session_id,
                    correlation_id=correlation_id,
                    operation=invocation.operation,
                    outcome=outcome,
                    duration_ms=min(duration_ms, MAX_JSON_SAFE_INTEGER),
                    error_code=error_code,
                    usage=usage,
                )
            )
        except BaseException:
            # Diagnostics are best effort and must not replace a product result,
            # exception, or cancellation.
            return


def compose_middleware(
    middleware: tuple[ApplicationMiddleware, ...],
    terminal: ApplicationCall,
) -> ApplicationCall:
    call = terminal
    for item in reversed(middleware):
        call = _wrap(item, call)
    return call


def _wrap(
    middleware: ApplicationMiddleware,
    next_call: ApplicationCall,
) -> ApplicationCall:
    async def invoke(invocation: ApplicationInvocation) -> object:
        return await middleware(invocation, next_call)

    return invoke


def _result_facts(
    result: object,
) -> tuple[ObservationOutcome, str | None, DiagnosticUsage | None]:
    if isinstance(result, ApplicationResult):
        if not result.ok:
            assert result.error is not None
            return "product_error", result.error.code.value, None
        result = result.value
    if isinstance(result, CommandError):
        return "product_error", _stable_error_code(result.code), None
    return "success", None, _usage(result)


def _stable_error_code(value: str) -> str:
    if (
        value
        and len(value) <= 128
        and value[0].isalpha()
        and value[0].isascii()
        and all(
            character.isascii()
            and (character.islower() or character.isdigit() or character == "_")
            for character in value
        )
    ):
        return value
    return "command_error"


def _usage(result: object) -> DiagnosticUsage | None:
    if isinstance(result, ApplicationState):
        return _usage_from_mapping(result.usage)
    if isinstance(result, CommandResult) and isinstance(
        result.payload,
        (CompactCommandPayload, UsageCommandPayload),
    ):
        return _usage_from_summary(result.payload.usage)
    return None


def _usage_from_summary(usage: UsageSummary) -> DiagnosticUsage:
    active_execution_ms = round(usage.active_execution_seconds * 1_000)
    return DiagnosticUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        model_calls=usage.model_calls,
        tool_calls=usage.tool_calls,
        provider_retries=usage.provider_retries,
        compressions=usage.compressions,
        web_requests=usage.web_requests,
        active_execution_ms=min(active_execution_ms, MAX_JSON_SAFE_INTEGER),
    )


def _usage_from_mapping(usage: Mapping[str, int | float]) -> DiagnosticUsage | None:
    integer_fields = (
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "model_calls",
        "tool_calls",
        "provider_retries",
        "compressions",
        "web_requests",
    )
    values: dict[str, int] = {}
    for field in integer_fields:
        value = usage.get(field, 0)
        if type(value) is not int or not 0 <= value <= MAX_JSON_SAFE_INTEGER:
            return None
        values[field] = value
    active_seconds = usage.get("active_execution_seconds", 0.0)
    if (
        type(active_seconds) not in {int, float}
        or not math.isfinite(active_seconds)
        or active_seconds < 0
        or active_seconds > MAX_JSON_SAFE_INTEGER / 1_000
    ):
        return None
    return DiagnosticUsage(
        **values,
        active_execution_ms=round(active_seconds * 1_000),
    )
