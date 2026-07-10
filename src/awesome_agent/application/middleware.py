from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from awesome_agent.application.contracts import ProductError
from awesome_agent.safety import redact_value

type ApplicationCall = Callable[["ApplicationInvocation"], Awaitable[object]]
type ObservationSink = Callable[["ApplicationObservation"], None]

_SENSITIVE_INPUT_KEYS = frozenset(
    {"command", "content", "input", "prompt", "query", "text"}
)


class ApplicationInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class ApplicationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    correlation_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    outcome: Literal["success", "product_error", "cancelled", "exception"]
    latency_seconds: float = Field(ge=0.0)
    safe_payload: dict[str, JsonValue] = Field(default_factory=dict)
    usage: dict[str, int] = Field(default_factory=dict)


class ApplicationMiddleware(Protocol):
    async def __call__(
        self,
        invocation: ApplicationInvocation,
        next_call: ApplicationCall,
    ) -> object: ...


class ObservationalMiddleware:
    """Correlation, trace/log data, latency/count outcome, and Usage observation."""

    def __init__(
        self,
        *,
        correlation_id: Callable[[], str],
        monotonic: Callable[[], float],
        sink: ObservationSink,
    ) -> None:
        self._correlation_id = correlation_id
        self._monotonic = monotonic
        self._sink = sink

    async def __call__(
        self,
        invocation: ApplicationInvocation,
        next_call: ApplicationCall,
    ) -> object:
        correlation_id = self._correlation_id()
        started = self._monotonic()
        try:
            result = await next_call(invocation)
        except asyncio.CancelledError:
            self._observe(
                invocation,
                correlation_id=correlation_id,
                started=started,
                outcome="cancelled",
            )
            raise
        except Exception:
            self._observe(
                invocation,
                correlation_id=correlation_id,
                started=started,
                outcome="exception",
            )
            raise
        self._observe(
            invocation,
            correlation_id=correlation_id,
            started=started,
            outcome="product_error" if isinstance(result, ProductError) else "success",
            usage=_usage(result),
        )
        return result

    def _observe(
        self,
        invocation: ApplicationInvocation,
        *,
        correlation_id: str,
        started: float,
        outcome: Literal["success", "product_error", "cancelled", "exception"],
        usage: dict[str, int] | None = None,
    ) -> None:
        self._sink(
            ApplicationObservation(
                correlation_id=correlation_id,
                name=invocation.name,
                outcome=outcome,
                latency_seconds=max(0.0, self._monotonic() - started),
                safe_payload=_safe_payload(invocation.payload),
                usage=usage or {},
            )
        )


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


def _safe_payload(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    safe: dict[str, JsonValue] = {}
    for key, value in payload.items():
        if key.casefold() in _SENSITIVE_INPUT_KEYS:
            safe[key] = "[REDACTED:content]"
            continue
        redacted, _ = redact_value(value)
        safe[key] = cast(JsonValue, redacted)
    return safe


def _usage(result: object) -> dict[str, int]:
    usage = getattr(result, "usage", None)
    if isinstance(usage, dict):
        return {
            str(key): value
            for key, value in usage.items()
            if isinstance(value, int) and value >= 0
        }
    return {}
