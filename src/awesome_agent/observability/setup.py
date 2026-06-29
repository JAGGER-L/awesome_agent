from opentelemetry.sdk.trace import TracerProvider

from awesome_agent.observability.otel import (
    OTelConfig,
    SafeSpanExporter,
    configure_otel,
)

__all__ = ["SafeSpanExporter", "configure_observability"]


def configure_observability(
    *,
    service_name: str = "awesome-agent",
    console_exporter: bool = True,
) -> TracerProvider:
    return configure_otel(
        OTelConfig(
            service_name=service_name,
            console_exporter=console_exporter,
        )
    )
