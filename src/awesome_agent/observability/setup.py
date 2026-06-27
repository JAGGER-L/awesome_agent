import logging
from collections.abc import Sequence

import structlog
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
    SpanExportResult,
)

logger = logging.getLogger(__name__)


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


def configure_observability(
    *,
    service_name: str = "awesome-agent",
    console_exporter: bool = True,
) -> TracerProvider:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if console_exporter:
        provider.add_span_processor(
            BatchSpanProcessor(SafeSpanExporter(ConsoleSpanExporter()))
        )
    trace.set_tracer_provider(provider)
    return provider
