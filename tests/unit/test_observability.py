from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from awesome_agent.observability.facade import (
    NoopObservabilityFacade,
    ObservabilityFacade,
    ObservabilitySpanInput,
)
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
async def test_facade_records_durable_and_otel_span() -> None:
    repository = InMemoryObservabilityRepository()
    exporter = RecordingExporter()
    facade = _facade(repository, exporter)
    run_id = uuid4()

    async with facade.start_span(
        ObservabilitySpanInput(
            run_id=run_id,
            name="run.execute",
            category="run",
            status="completed",
            attributes={
                "prompt": "secret prompt",
                "runtime_route": "solo-readonly",
                "oversized": "x" * 600,
            },
        )
    ) as durable:
        assert durable.name == "run.execute"

    durable_spans = await repository.list_spans_for_run(run_id)
    assert durable_spans[0].name == "run.execute"
    assert durable_spans[0].trace_id == run_id.hex
    assert durable_spans[0].attributes["runtime_route"] == "solo-readonly"
    assert durable_spans[0].attributes["oversized"] == "x" * 500
    assert "prompt" not in durable_spans[0].attributes
    assert exporter.spans[0].name == "run.execute"
    otel_attributes = exporter.spans[0].attributes
    assert otel_attributes is not None
    assert otel_attributes["awesome.run_id"] == str(run_id)
    assert otel_attributes["awesome.trace_id"] == run_id.hex
    assert "prompt" not in otel_attributes


@pytest.mark.asyncio
async def test_facade_records_metric_and_model_call_with_safe_attributes() -> None:
    repository = InMemoryObservabilityRepository()
    facade = _facade(repository, RecordingExporter())
    run_id = uuid4()
    agent_id = uuid4()

    metric = await facade.record_metric(
        run_id=run_id,
        name="run.duration_ms",
        value=25,
        unit="ms",
        attributes={"status": "completed", "headers": "secret"},
    )
    model_call = await facade.record_model_call(
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
        )
    )

    assert metric.attributes == {"status": "completed"}
    assert await repository.list_metrics_for_run(run_id) == [metric]
    assert await repository.list_model_calls_for_run(run_id) == [model_call]


@pytest.mark.asyncio
async def test_facade_isolates_repository_failures() -> None:
    facade = _facade(FailingObservabilityRepository(), RecordingExporter())
    run_id = uuid4()

    span = await facade.record_span(
        ObservabilitySpanInput(
            run_id=run_id,
            name="run.execute",
            category="run",
            status="completed",
        )
    )
    metric = await facade.record_metric(
        run_id=run_id,
        name="run.duration_ms",
        value=25,
        unit="ms",
    )
    model_call = await facade.record_model_call(
        DurableModelCall(
            run_id=run_id,
            agent_id=None,
            turn=1,
            provider="deepseek",
            model="deepseek-v4-flash",
            status="failed",
        )
    )

    assert span.name == "run.execute"
    assert metric.name == "run.duration_ms"
    assert model_call.status == "failed"


@pytest.mark.asyncio
async def test_noop_facade_accepts_observability_calls() -> None:
    facade = NoopObservabilityFacade()
    run_id = uuid4()

    async with facade.start_span(
        ObservabilitySpanInput(
            run_id=run_id,
            name="run.execute",
            category="run",
            status="completed",
        )
    ) as span:
        assert span.name == "run.execute"

    assert (
        await facade.record_metric(
            run_id=run_id,
            name="run.duration_ms",
            value=25,
            unit="ms",
        )
    ).name == "run.duration_ms"


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


class RecordingExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


class FailingObservabilityRepository:
    async def record_span(self, span: DurableSpan) -> DurableSpan:
        raise RuntimeError("span repository unavailable")

    async def record_metric(self, metric: DurableMetric) -> DurableMetric:
        raise RuntimeError("metric repository unavailable")

    async def record_model_call(self, call: DurableModelCall) -> DurableModelCall:
        raise RuntimeError("model call repository unavailable")

    async def list_spans_for_run(self, run_id: UUID) -> list[DurableSpan]:
        return []

    async def list_metrics_for_run(self, run_id: UUID) -> list[DurableMetric]:
        return []

    async def list_model_calls_for_run(self, run_id: UUID) -> list[DurableModelCall]:
        return []


def _facade(
    repository: object,
    exporter: RecordingExporter,
) -> ObservabilityFacade:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return ObservabilityFacade(
        repository=repository,  # type: ignore[arg-type]
        tracer=provider.get_tracer("test"),
    )
