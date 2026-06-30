from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import structlog
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricExporter,
    MetricExportResult,
    MetricsData,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
    SpanExportResult,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OTelConfig:
    service_name: str = "awesome-agent"
    process_kind: str = "unknown"
    console_exporter: bool = True
    otlp_endpoint: str | None = None


class SafeSpanExporter(SpanExporter):
    def __init__(self, exporter: SpanExporter) -> None:
        self._exporter = exporter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            return self._exporter.export(spans)
        except Exception:
            logger.exception("OpenTelemetry exporter failed.")
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self._exporter.shutdown()
        except Exception:
            logger.exception("OpenTelemetry exporter shutdown failed.")

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            return self._exporter.force_flush(timeout_millis)
        except Exception:
            logger.exception("OpenTelemetry exporter flush failed.")
            return False


class SafeMetricExporter(MetricExporter):
    def __init__(self, exporter: MetricExporter) -> None:
        super().__init__(
            preferred_temporality=getattr(exporter, "_preferred_temporality", None),
            preferred_aggregation=getattr(exporter, "_preferred_aggregation", None),
        )
        self._exporter = exporter

    def export(
        self,
        metrics_data: MetricsData,
        timeout_millis: float = 10000,
        **kwargs: object,
    ) -> MetricExportResult:
        try:
            return self._exporter.export(metrics_data, timeout_millis, **kwargs)
        except Exception:
            logger.exception("OpenTelemetry metric exporter failed.")
            return MetricExportResult.FAILURE

    def shutdown(self, timeout_millis: float = 30000, **kwargs: object) -> None:
        try:
            self._exporter.shutdown(timeout_millis, **kwargs)
        except Exception:
            logger.exception("OpenTelemetry metric exporter shutdown failed.")

    def force_flush(self, timeout_millis: float = 10000) -> bool:
        try:
            return self._exporter.force_flush(timeout_millis)
        except Exception:
            logger.exception("OpenTelemetry metric exporter flush failed.")
            return False


class OTelMetricRecorder:
    def __init__(self, meter: Meter) -> None:
        self._meter = meter
        self._counters: dict[tuple[str, str], object] = {}
        self._histograms: dict[tuple[str, str], object] = {}

    def record_metric(
        self,
        *,
        name: str,
        value: float,
        unit: str,
        attributes: Mapping[str, object],
    ) -> None:
        metric_key = (name, unit)
        if _is_counter_metric(name):
            counter = self._counters.get(metric_key)
            if counter is None:
                counter = self._meter.create_counter(name=name, unit=unit)
                self._counters[metric_key] = counter
            counter.add(max(0, value), attributes=dict(attributes))  # type: ignore[attr-defined]
            return
        histogram = self._histograms.get(metric_key)
        if histogram is None:
            histogram = self._meter.create_histogram(name=name, unit=unit)
            self._histograms[metric_key] = histogram
        histogram.record(value, attributes=dict(attributes))  # type: ignore[attr-defined]


def configure_otel(config: OTelConfig) -> TracerProvider:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": config.service_name,
                "awesome.process_kind": config.process_kind,
            }
        )
    )
    if config.console_exporter:
        _add_exporter(provider, ConsoleSpanExporter())
    if config.otlp_endpoint:
        try:
            _add_exporter(provider, OTLPSpanExporter(endpoint=config.otlp_endpoint))
        except Exception:
            logger.exception("OpenTelemetry OTLP exporter initialization failed.")
    try:
        trace.set_tracer_provider(provider)
    except Exception:
        logger.exception("OpenTelemetry tracer provider setup failed.")
    return provider


def configure_otel_metrics(config: OTelConfig) -> OTelMetricRecorder:
    provider = MeterProvider(
        resource=_resource(config),
        metric_readers=tuple(_metric_readers(config)),
    )
    try:
        metrics.set_meter_provider(provider)
    except Exception:
        logger.exception("OpenTelemetry meter provider setup failed.")
    return OTelMetricRecorder(provider.get_meter("awesome_agent"))


def get_tracer(name: str = "awesome_agent") -> trace.Tracer:
    return trace.get_tracer(name)


def _add_exporter(provider: TracerProvider, exporter: SpanExporter) -> None:
    provider.add_span_processor(BatchSpanProcessor(SafeSpanExporter(exporter)))


def _metric_readers(config: OTelConfig) -> list[PeriodicExportingMetricReader]:
    readers: list[PeriodicExportingMetricReader] = []
    if config.console_exporter:
        readers.append(PeriodicExportingMetricReader(SafeMetricExporter(ConsoleMetricExporter())))
    if config.otlp_endpoint:
        try:
            readers.append(
                PeriodicExportingMetricReader(
                    SafeMetricExporter(OTLPMetricExporter(endpoint=config.otlp_endpoint))
                )
            )
        except Exception:
            logger.exception(
                "OpenTelemetry OTLP metric exporter initialization failed."
            )
    return readers


def _resource(config: OTelConfig) -> Resource:
    return Resource.create(
        {
            "service.name": config.service_name,
            "awesome.process_kind": config.process_kind,
        }
    )


def _is_counter_metric(name: str) -> bool:
    return name.endswith(".count") or name.endswith(".tokens")
