from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from awesome_agent.observability.otel import OTelConfig, configure_otel
from awesome_agent.observability.repository import (
    DurableMetric,
    DurableModelCall,
    DurableSpan,
    InMemoryObservabilityRepository,
)
from awesome_agent.observability.setup import SafeSpanExporter, configure_observability


def test_observability_configures_service_resource() -> None:
    provider = configure_observability(
        service_name="awesome-agent-test",
        console_exporter=False,
    )

    assert provider.resource.attributes["service.name"] == "awesome-agent-test"


def test_otel_configures_process_kind_resource() -> None:
    provider = configure_otel(
        OTelConfig(
            service_name="awesome-agent-test",
            process_kind="worker",
            console_exporter=False,
            otlp_endpoint=None,
        )
    )

    assert provider.resource.attributes["service.name"] == "awesome-agent-test"
    assert provider.resource.attributes["awesome.process_kind"] == "worker"


def test_otel_respects_console_exporter_toggle() -> None:
    provider = configure_otel(
        OTelConfig(
            service_name="awesome-agent-test",
            process_kind="api",
            console_exporter=False,
            otlp_endpoint=None,
        )
    )

    processor = provider._active_span_processor
    assert processor._span_processors == ()


def test_otel_configures_otlp_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    created_endpoints: list[str | None] = []

    class RecordingOTLPExporter(FailingExporter):
        def __init__(self, *, endpoint: str | None = None) -> None:
            created_endpoints.append(endpoint)

        def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
            return SpanExportResult.SUCCESS

    monkeypatch.setattr(
        "awesome_agent.observability.otel.OTLPSpanExporter",
        RecordingOTLPExporter,
    )

    provider = configure_otel(
        OTelConfig(
            service_name="awesome-agent-test",
            process_kind="worker",
            console_exporter=False,
            otlp_endpoint="http://collector.example/v1/traces",
        )
    )

    assert created_endpoints == ["http://collector.example/v1/traces"]
    processor = provider._active_span_processor
    assert len(processor._span_processors) == 1


def test_otel_continues_when_otlp_exporter_initialization_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenOTLPExporter(FailingExporter):
        def __init__(self, *, endpoint: str | None = None) -> None:
            raise RuntimeError("collector unavailable")

    monkeypatch.setattr(
        "awesome_agent.observability.otel.OTLPSpanExporter",
        BrokenOTLPExporter,
    )

    provider = configure_otel(
        OTelConfig(
            service_name="awesome-agent-test",
            process_kind="worker",
            console_exporter=False,
            otlp_endpoint="http://collector.example/v1/traces",
        )
    )

    assert provider.resource.attributes["awesome.process_kind"] == "worker"
    processor = provider._active_span_processor
    assert processor._span_processors == ()


def test_safe_span_exporter_isolates_exporter_failures() -> None:
    exporter = SafeSpanExporter(FailingExporter())

    assert exporter.export(()) is SpanExportResult.FAILURE


@pytest.mark.asyncio
async def test_in_memory_repository_records_observability_evidence() -> None:
    repository = InMemoryObservabilityRepository()
    run_id = uuid4()
    agent_id = uuid4()
    started = datetime.now(UTC)
    ended = started + timedelta(milliseconds=25)

    span = await repository.record_span(
        DurableSpan(
            run_id=run_id,
            trace_id=run_id.hex,
            span_id="0000000000000001",
            parent_span_id=None,
            name="run.execute",
            category="run",
            status="completed",
            started_at=started,
            ended_at=ended,
            duration_ms=25,
            attributes={"graph": "solo-readonly"},
        )
    )
    metric = await repository.record_metric(
        DurableMetric(
            run_id=run_id,
            name="run.duration_ms",
            value=25,
            unit="ms",
            attributes={"status": "completed"},
        )
    )
    model_call = await repository.record_model_call(
        DurableModelCall(
            run_id=run_id,
            agent_id=agent_id,
            turn=1,
            provider="deepseek",
            model="deepseek-v4-flash",
            status="completed",
            stop_reason="completed",
            input_tokens=10,
            output_tokens=20,
            latency_ms=25,
            trace_id=run_id.hex,
            span_id="0000000000000002",
        )
    )

    assert await repository.list_spans_for_run(run_id) == [span]
    assert await repository.list_metrics_for_run(run_id) == [metric]
    assert await repository.list_model_calls_for_run(run_id) == [model_call]


class FailingExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        raise RuntimeError("exporter unavailable")

    def shutdown(self) -> None:
        return None
