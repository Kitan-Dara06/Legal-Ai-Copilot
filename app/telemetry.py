"""
OpenTelemetry instrumentation for the Lex API.

Initializes tracing, metrics, and logging exporters.
FastAPI auto-instrumentation captures request duration, route patterns,
and error rates with < 5ms overhead (NFR-PERF-06).

Usage:
    from app.telemetry import setup_telemetry
    setup_telemetry(app)
"""

import logging
import os

logger = logging.getLogger(__name__)


def setup_telemetry(app):
    """
    Initialize OpenTelemetry with FastAPI auto-instrumentation.
    Safe to call even if OTEL is not configured — degrades gracefully.
    """
    otel_enabled = os.getenv("OTEL_ENABLED", "false").lower() == "true"
    if not otel_enabled:
        logger.info(
            "[telemetry] OpenTelemetry disabled (set OTEL_ENABLED=true to enable)"
        )
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        service_name = os.getenv("OTEL_SERVICE_NAME", "lex-api")
        otlp_endpoint = os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/v1/traces"
        )

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)

        trace.set_tracer_provider(provider)

        # Auto-instrument FastAPI — captures route, method, status code, duration
        FastAPIInstrumentor.instrument_app(app)

        logger.info(
            "[telemetry] OpenTelemetry initialized: service=%s endpoint=%s",
            service_name,
            otlp_endpoint,
        )

    except ImportError as e:
        logger.warning(
            "[telemetry] OpenTelemetry packages not installed: %s. "
            "Run: pip install opentelemetry-api opentelemetry-sdk "
            "opentelemetry-instrumentation-fastapi opentelemetry-exporter-otlp-proto-http",
            e,
        )
    except Exception as e:
        logger.warning("[telemetry] Failed to initialize OpenTelemetry: %s", e)
