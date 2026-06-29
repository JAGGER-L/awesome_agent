from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
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


def get_tracer(name: str = "awesome_agent") -> trace.Tracer:
    return trace.get_tracer(name)


def _add_exporter(provider: TracerProvider, exporter: SpanExporter) -> None:
    provider.add_span_processor(BatchSpanProcessor(SafeSpanExporter(exporter)))
