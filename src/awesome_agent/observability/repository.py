from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.persistence.models import (
    ModelCallRecord,
    ObservabilityMetricRecord,
    ObservabilitySpanRecord,
)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class DurableSpan:
    run_id: UUID
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    category: str
    status: str
    id: UUID = field(default_factory=uuid4)
    started_at: datetime = field(default_factory=_now)
    ended_at: datetime | None = None
    duration_ms: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DurableMetric:
    run_id: UUID | None
    name: str
    value: float
    unit: str
    id: UUID = field(default_factory=uuid4)
    attributes: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now)


@dataclass(frozen=True, slots=True)
class DurableModelCall:
    run_id: UUID
    agent_id: UUID | None
    turn: int
    provider: str
    model: str
    status: str
    id: UUID = field(default_factory=uuid4)
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    latency_ms: int | None = None
    trace_id: str | None = None
    span_id: str | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=_now)


class ObservabilityRepository(Protocol):
    async def record_span(self, span: DurableSpan) -> DurableSpan:
        """Persist one observed span."""
        ...

    async def record_metric(self, metric: DurableMetric) -> DurableMetric:
        """Persist one metric point."""
        ...

    async def record_model_call(self, call: DurableModelCall) -> DurableModelCall:
        """Persist one model-call summary."""
        ...

    async def list_spans_for_run(self, run_id: UUID) -> list[DurableSpan]:
        """Load spans for a run in timeline order."""
        ...

    async def list_metrics_for_run(self, run_id: UUID) -> list[DurableMetric]:
        """Load metrics for a run in creation order."""
        ...

    async def list_model_calls_for_run(self, run_id: UUID) -> list[DurableModelCall]:
        """Load model calls for a run in turn order."""
        ...


class InMemoryObservabilityRepository:
    def __init__(self) -> None:
        self._spans: dict[UUID, DurableSpan] = {}
        self._metrics: dict[UUID, DurableMetric] = {}
        self._model_calls: dict[UUID, DurableModelCall] = {}

    async def record_span(self, span: DurableSpan) -> DurableSpan:
        self._spans[span.id] = span
        return span

    async def record_metric(self, metric: DurableMetric) -> DurableMetric:
        self._metrics[metric.id] = metric
        return metric

    async def record_model_call(self, call: DurableModelCall) -> DurableModelCall:
        self._model_calls[call.id] = call
        return call

    async def list_spans_for_run(self, run_id: UUID) -> list[DurableSpan]:
        return sorted(
            (span for span in self._spans.values() if span.run_id == run_id),
            key=lambda span: (span.started_at, span.id),
        )

    async def list_metrics_for_run(self, run_id: UUID) -> list[DurableMetric]:
        return sorted(
            (metric for metric in self._metrics.values() if metric.run_id == run_id),
            key=lambda metric: (metric.created_at, metric.id),
        )

    async def list_model_calls_for_run(self, run_id: UUID) -> list[DurableModelCall]:
        return sorted(
            (call for call in self._model_calls.values() if call.run_id == run_id),
            key=lambda call: (call.turn, call.created_at, call.id),
        )


class NoopObservabilityRepository:
    async def record_span(self, span: DurableSpan) -> DurableSpan:
        return span

    async def record_metric(self, metric: DurableMetric) -> DurableMetric:
        return metric

    async def record_model_call(self, call: DurableModelCall) -> DurableModelCall:
        return call

    async def list_spans_for_run(self, run_id: UUID) -> list[DurableSpan]:
        return []

    async def list_metrics_for_run(self, run_id: UUID) -> list[DurableMetric]:
        return []

    async def list_model_calls_for_run(self, run_id: UUID) -> list[DurableModelCall]:
        return []


class PostgresObservabilityRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def record_span(self, span: DurableSpan) -> DurableSpan:
        if span.run_id.int == 0:
            return span
        async with self._sessions.begin() as session:
            record = await session.get(ObservabilitySpanRecord, span.id)
            if record is None:
                session.add(_span_to_record(span))
            else:
                _update_span_record(record, span)
        return span

    async def record_metric(self, metric: DurableMetric) -> DurableMetric:
        async with self._sessions.begin() as session:
            record = await session.get(ObservabilityMetricRecord, metric.id)
            if record is None:
                session.add(_metric_to_record(metric))
            else:
                _update_metric_record(record, metric)
        return metric

    async def record_model_call(self, call: DurableModelCall) -> DurableModelCall:
        async with self._sessions.begin() as session:
            record = await session.get(ModelCallRecord, call.id)
            if record is None:
                session.add(_model_call_to_record(call))
            else:
                _update_model_call_record(record, call)
        return call

    async def list_spans_for_run(self, run_id: UUID) -> list[DurableSpan]:
        async with self._sessions() as session:
            records = list(
                await session.scalars(
                    select(ObservabilitySpanRecord)
                    .where(ObservabilitySpanRecord.run_id == run_id)
                    .order_by(
                        ObservabilitySpanRecord.started_at,
                        ObservabilitySpanRecord.id,
                    )
                )
            )
        return [_span_from_record(record) for record in records]

    async def list_metrics_for_run(self, run_id: UUID) -> list[DurableMetric]:
        async with self._sessions() as session:
            records = list(
                await session.scalars(
                    select(ObservabilityMetricRecord)
                    .where(ObservabilityMetricRecord.run_id == run_id)
                    .order_by(
                        ObservabilityMetricRecord.created_at,
                        ObservabilityMetricRecord.id,
                    )
                )
            )
        return [_metric_from_record(record) for record in records]

    async def list_model_calls_for_run(self, run_id: UUID) -> list[DurableModelCall]:
        async with self._sessions() as session:
            records = list(
                await session.scalars(
                    select(ModelCallRecord)
                    .where(ModelCallRecord.run_id == run_id)
                    .order_by(
                        ModelCallRecord.turn,
                        ModelCallRecord.created_at,
                        ModelCallRecord.id,
                    )
                )
            )
        return [_model_call_from_record(record) for record in records]


def _span_to_record(span: DurableSpan) -> ObservabilitySpanRecord:
    return ObservabilitySpanRecord(
        id=span.id,
        run_id=span.run_id,
        trace_id=span.trace_id,
        span_id=span.span_id,
        parent_span_id=span.parent_span_id,
        name=span.name,
        category=span.category,
        status=span.status,
        started_at=span.started_at,
        ended_at=span.ended_at,
        duration_ms=span.duration_ms,
        attributes=span.attributes,
        error=span.error,
    )


def _update_span_record(
    record: ObservabilitySpanRecord,
    span: DurableSpan,
) -> None:
    record.status = span.status
    record.ended_at = span.ended_at
    record.duration_ms = span.duration_ms
    record.attributes = span.attributes
    record.error = span.error


def _span_from_record(record: ObservabilitySpanRecord) -> DurableSpan:
    return DurableSpan(
        id=record.id,
        run_id=record.run_id,
        trace_id=record.trace_id,
        span_id=record.span_id,
        parent_span_id=record.parent_span_id,
        name=record.name,
        category=record.category,
        status=record.status,
        started_at=record.started_at,
        ended_at=record.ended_at,
        duration_ms=record.duration_ms,
        attributes={str(key): value for key, value in record.attributes.items()},
        error=record.error,
    )


def _metric_to_record(metric: DurableMetric) -> ObservabilityMetricRecord:
    return ObservabilityMetricRecord(
        id=metric.id,
        run_id=metric.run_id,
        name=metric.name,
        value=metric.value,
        unit=metric.unit,
        attributes=metric.attributes,
        created_at=metric.created_at,
    )


def _update_metric_record(
    record: ObservabilityMetricRecord,
    metric: DurableMetric,
) -> None:
    record.value = metric.value
    record.unit = metric.unit
    record.attributes = metric.attributes


def _metric_from_record(record: ObservabilityMetricRecord) -> DurableMetric:
    return DurableMetric(
        id=record.id,
        run_id=record.run_id,
        name=record.name,
        value=record.value,
        unit=record.unit,
        attributes={str(key): value for key, value in record.attributes.items()},
        created_at=record.created_at,
    )


def _model_call_to_record(call: DurableModelCall) -> ModelCallRecord:
    return ModelCallRecord(
        id=call.id,
        run_id=call.run_id,
        agent_id=call.agent_id,
        turn=call.turn,
        provider=call.provider,
        model=call.model,
        status=call.status,
        stop_reason=call.stop_reason,
        input_tokens=call.input_tokens,
        output_tokens=call.output_tokens,
        reasoning_tokens=call.reasoning_tokens,
        cache_read_tokens=call.cache_read_tokens,
        cache_write_tokens=call.cache_write_tokens,
        latency_ms=call.latency_ms,
        trace_id=call.trace_id,
        span_id=call.span_id,
        error=call.error,
        created_at=call.created_at,
    )


def _update_model_call_record(
    record: ModelCallRecord,
    call: DurableModelCall,
) -> None:
    record.status = call.status
    record.stop_reason = call.stop_reason
    record.input_tokens = call.input_tokens
    record.output_tokens = call.output_tokens
    record.reasoning_tokens = call.reasoning_tokens
    record.cache_read_tokens = call.cache_read_tokens
    record.cache_write_tokens = call.cache_write_tokens
    record.latency_ms = call.latency_ms
    record.trace_id = call.trace_id
    record.span_id = call.span_id
    record.error = call.error


def _model_call_from_record(record: ModelCallRecord) -> DurableModelCall:
    return DurableModelCall(
        id=record.id,
        run_id=record.run_id,
        agent_id=record.agent_id,
        turn=record.turn,
        provider=record.provider,
        model=record.model,
        status=record.status,
        stop_reason=record.stop_reason,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        reasoning_tokens=record.reasoning_tokens,
        cache_read_tokens=record.cache_read_tokens,
        cache_write_tokens=record.cache_write_tokens,
        latency_ms=record.latency_ms,
        trace_id=record.trace_id,
        span_id=record.span_id,
        error=record.error,
        created_at=record.created_at,
    )
