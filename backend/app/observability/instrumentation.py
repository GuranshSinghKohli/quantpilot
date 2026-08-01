"""Phase 12 — optional LangSmith, OpenTelemetry, and Sentry wiring.

All integrations are no-ops when their env vars / packages are absent so local
dev and CI stay lightweight.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from app.observability.logger import get_logger, log_event

logger = get_logger("instrumentation")


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def configure_langsmith() -> bool:
    """Enable LangSmith / LangChain tracing when a key is present.

    LangGraph/LangChain read LANGCHAIN_TRACING_V2 + LANGCHAIN_API_KEY (or
    LANGSMITH_API_KEY) from the environment. We normalize those vars here.
    """
    api_key = (
        os.getenv("LANGSMITH_API_KEY", "").strip()
        or os.getenv("LANGCHAIN_API_KEY", "").strip()
    )
    enabled = _truthy("LANGSMITH_TRACING", "false") or _truthy(
        "LANGCHAIN_TRACING_V2", "false"
    )
    if not api_key or not enabled:
        return False

    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    if not os.getenv("LANGCHAIN_API_KEY"):
        os.environ["LANGCHAIN_API_KEY"] = api_key
    project = (
        os.getenv("LANGSMITH_PROJECT", "").strip()
        or os.getenv("LANGCHAIN_PROJECT", "").strip()
        or "quantpilot"
    )
    os.environ.setdefault("LANGCHAIN_PROJECT", project)
    os.environ.setdefault("LANGSMITH_PROJECT", project)
    log_event(logger, logging.INFO, "LangSmith tracing enabled", project=project)
    return True


def configure_sentry() -> bool:
    """Initialize Sentry when SENTRY_DSN is set."""
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        log_event(
            logger,
            logging.WARNING,
            "SENTRY_DSN set but sentry-sdk is not installed",
        )
        return False

    sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1") or "0.1")
    environment = os.getenv("SENTRY_ENVIRONMENT", os.getenv("ENVIRONMENT", "development"))
    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
        traces_sample_rate=sample_rate,
        environment=environment,
        send_default_pii=False,
    )
    log_event(
        logger,
        logging.INFO,
        "Sentry initialized",
        environment=environment,
        traces_sample_rate=sample_rate,
    )
    return True


def configure_opentelemetry(app: Optional[Any] = None) -> bool:
    """Attach FastAPI OpenTelemetry instrumentation when OTEL_EXPORTER is set."""
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint and not _truthy("OTEL_ENABLED", "false"):
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except ImportError:
        log_event(
            logger,
            logging.WARNING,
            "OpenTelemetry requested but opentelemetry packages are not installed",
        )
        return False

    service_name = os.getenv("OTEL_SERVICE_NAME", "quantpilot-api")
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        except ImportError:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            log_event(
                logger,
                logging.WARNING,
                "OTLP exporter missing; falling back to console spans",
            )
    elif _truthy("OTEL_CONSOLE_EXPORT", "false"):
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)

    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app)
        except ImportError:
            log_event(
                logger,
                logging.WARNING,
                "opentelemetry-instrumentation-fastapi not installed",
            )
            return False

    log_event(
        logger,
        logging.INFO,
        "OpenTelemetry tracing enabled",
        service_name=service_name,
        endpoint=endpoint or "console",
    )
    return True


def init_observability(app: Optional[Any] = None) -> dict:
    """Configure all Phase 12 observability backends. Safe to call once at startup."""
    status = {
        "langsmith": configure_langsmith(),
        "sentry": configure_sentry(),
        "otel": configure_opentelemetry(app),
    }
    log_event(logger, logging.INFO, "Observability bootstrap complete", **status)
    return status
