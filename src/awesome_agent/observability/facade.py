from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from awesome_agent.observability.otel import get_tracer
from awesome_agent.observability.repository import (
    DurableMetric,
    DurableModelCall,
    DurableSpan,
    NoopObservabilityRepository,
    ObservabilityRepository,
)

logger = logging.getLogger(__name__)

_ATTRIBUTE_LIMIT = 500
_SENSITIVE_ATTRIBUTE_KEYS = {
    "prompt",
    "messages",
    "tool_result",
    "patch",
    "authorization",
    "api_key",
    "secret",
    "headers",
    "continuation",
}
AttributeValue = str | bool | int | float


@dataclass(frozen=True, slots=True)
class ObservabilitySpanInput:
    run_id: UUID
    name: str
    category: str
    status: str
    attributes: dict[str, object] = field(default_factory=dict)
    trace_id: str | None = None
    durable_span_id: str | None = None
    parent_span_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None


class ObservabilityFacade:
    def __init__(
        self,
        *,
        repository: ObservabilityRepository,
        tracer: trace.Tracer | None = None,
    ) -> None:
        self._repository = repository
        self._tracer = tracer or get_tracer()

    @asynccontextmanager
    async def start_span(
        self,
        span: ObservabilitySpanInput,
    ) -> AsyncIterator[DurableSpan]:
        started_at = span.started_at or _now()
        durable = self._durable_span(span, started_at=started_at)
        otel_attributes = self._otel_attributes(durable)
        final_status = span.status
        error_summary = span.error
        with self._tracer.start_as_current_span(
            span.name,
            attributes=otel_attributes,
        ) as otel_span:
            try:
                yield durable
            except Exception as error:
                final_status = "failed"
                error_summary = _bounded(str(error))
                otel_span.record_exception(error)
                otel_span.set_status(Status(StatusCode.ERROR, error_summary))
                raise
            finally:
                ended_at = span.ended_at or _now()
                final = replace(
                    durable,
                    status=final_status,
                    ended_at=ended_at,
                    duration_ms=span.duration_ms
                    if span.duration_ms is not None
                    else _duration_ms(started_at, ended_at),
                    error=error_summary,
                )
                await self._safe_record_span(final)

    span = start_span

    async def record_span(self, span: ObservabilitySpanInput) -> DurableSpan:
        durable = self._durable_span(span, started_at=span.started_at or _now())
        with self._tracer.start_as_current_span(
            durable.name,
            attributes=self._otel_attributes(durable),
        ) as otel_span:
            if durable.status == "failed" or durable.error:
                otel_span.set_status(
                    Status(StatusCode.ERROR, _bounded(durable.error or "failed"))
                )
        return await self._safe_record_span(durable)

    async def record_metric(
        self,
        *,
        run_id: UUID | None,
        name: str,
        value: float,
        unit: str,
        attributes: dict[str, object] | None = None,
    ) -> DurableMetric:
        metric = DurableMetric(
            run_id=run_id,
            name=name,
            value=value,
            unit=unit,
            attributes=_safe_attributes(attributes or {}),
        )
        try:
            return await self._repository.record_metric(metric)
        except Exception:
            logger.exception("Observability metric write failed.")
            return metric

    async def record_model_call(self, call: DurableModelCall) -> DurableModelCall:
        try:
            return await self._repository.record_model_call(call)
        except Exception:
            logger.exception("Observability model-call write failed.")
            return call

    def _durable_span(
        self,
        span: ObservabilitySpanInput,
        *,
        started_at: datetime,
    ) -> DurableSpan:
        return DurableSpan(
            run_id=span.run_id,
            trace_id=span.trace_id or span.run_id.hex,
            span_id=span.durable_span_id or uuid4().hex[:16],
            parent_span_id=span.parent_span_id,
            name=span.name,
            category=span.category,
            status=span.status,
            started_at=started_at,
            ended_at=span.ended_at,
            duration_ms=span.duration_ms,
            attributes=_safe_attributes(span.attributes),
            error=_bounded(span.error) if span.error else None,
        )

    def _otel_attributes(self, span: DurableSpan) -> dict[str, AttributeValue]:
        attributes: dict[str, AttributeValue] = {
            "awesome.run_id": str(span.run_id),
            "awesome.trace_id": span.trace_id,
            "awesome.durable_span_id": span.span_id,
        }
        if span.parent_span_id is not None:
            attributes["awesome.parent_span_id"] = span.parent_span_id
        attributes.update(span.attributes)
        return attributes

    async def _safe_record_span(self, span: DurableSpan) -> DurableSpan:
        try:
            return await self._repository.record_span(span)
        except Exception:
            logger.exception("Observability span write failed.")
            return span


class NoopObservabilityFacade(ObservabilityFacade):
    def __init__(self) -> None:
        super().__init__(repository=NoopObservabilityRepository())


def _safe_attributes(attributes: dict[str, object]) -> dict[str, AttributeValue]:
    safe: dict[str, AttributeValue] = {}
    for key, value in attributes.items():
        if _is_sensitive_key(key):
            continue
        safe[str(key)] = _safe_attribute_value(value)
    return safe


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(sensitive in normalized for sensitive in _SENSITIVE_ATTRIBUTE_KEYS)


def _safe_attribute_value(value: object) -> AttributeValue:
    if isinstance(value, str):
        return _bounded(value)
    if isinstance(value, bool | int | float):
        return value
    if value is None:
        return "null"
    return _bounded(str(value))


def _bounded(value: str) -> str:
    return value[:_ATTRIBUTE_LIMIT]


def _now() -> datetime:
    return datetime.now(UTC)


def _duration_ms(started_at: datetime, ended_at: datetime) -> int:
    return max(0, int((ended_at - started_at).total_seconds() * 1000))
