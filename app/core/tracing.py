"""Project-wide OpenTelemetry tracing scaffold.

Tracing is OFF by default. Setting ``OTEL_EXPORTER_OTLP_ENDPOINT`` in
the env turns it on with no other code change. With it set, every
HTTP request through FastAPI, every SQLAlchemy query, every httpx
outbound call, and every Redis operation produces a span exported
to the configured collector via OTLP/HTTP.

Why OFF by default:
- Local dev rarely runs an OTLP collector. Forcing it would add a
  dep on Jaeger/Tempo/etc. for `make compose-up` to work.
- Tests would need to mock the collector or be slow waiting for
  exports to flush. Skipping the install in tests keeps the suite fast.
- Production operators choose their backend (Jaeger, Tempo, Grafana
  Cloud, Honeycomb, etc.) — we don't pick for them.

What turning it on costs:
- ~30ms additional cold-start (instrumentation registration).
- ~50–150 µs per span (depends on attribute count).
- Network egress to the collector — typically same VPC, negligible.
- Memory: ~40MB (the instrumentation + SDK).

Sampling:
- ``otel_traces_sample_ratio`` defaults to 1.0 (all spans). For
  production, lower to 0.1–0.25 to keep collector cost reasonable.
- The TraceIdRatioBased sampler is biased toward consistent decisions:
  if a parent span is sampled, children also sample.

Operator runbook + integration tips: ``docs/OBSERVABILITY_TRACING.md``.
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

_log = get_logger("hypershop.tracing")
_initialized = False


def tracing_enabled() -> bool:
    """Cheap predicate. Safe to call from hot paths."""
    return bool(get_settings().otel_exporter_otlp_endpoint)


def init_tracing(*, service_name: str | None = None) -> None:
    """Wire up the OTLP exporter + global TracerProvider.

    Idempotent — calling more than once is a no-op (sub-process workers
    that import this module multiple times don't re-register).

    The api process calls this once during ``create_app()``; the worker
    process calls it once during ``_startup``. Each gets its own
    ``service.name`` so traces from the two processes are separable
    in the trace UI.
    """
    global _initialized
    if _initialized:
        return

    s = get_settings()
    endpoint = s.otel_exporter_otlp_endpoint
    if not endpoint:
        # Tracing intentionally disabled. Don't even import the SDK —
        # every import costs ~30 ms.
        return

    # Lazy imports — keep the cold-start cost in the hot path off the
    # tracing-disabled deployment.
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import (
        ParentBased,
        TraceIdRatioBased,
    )

    headers: dict[str, str] = {}
    if s.otel_exporter_otlp_headers is not None:
        # Format: "key1=val1,key2=val2" — same as the OTel CLI accepts.
        for pair in s.otel_exporter_otlp_headers.get_secret_value().split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                headers[k.strip()] = v.strip()

    resource = Resource.create(
        {
            "service.name": service_name or s.otel_service_name,
            "service.namespace": "hypershop",
            "deployment.environment": s.otel_environment,
        },
    )
    sampler = ParentBased(root=TraceIdRatioBased(s.otel_traces_sample_ratio))
    provider = TracerProvider(resource=resource, sampler=sampler)

    # Trace endpoint convention: collectors expose ``/v1/traces`` for
    # OTLP/HTTP. We let the user pass either the base URL (we append
    # /v1/traces) or the full URL.
    if not endpoint.rstrip("/").endswith("/v1/traces"):
        traces_endpoint = endpoint.rstrip("/") + "/v1/traces"
    else:
        traces_endpoint = endpoint

    exporter = OTLPSpanExporter(
        endpoint=traces_endpoint,
        headers=headers or None,
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    _log.info(
        "tracing_initialized",
        service_name=resource.attributes.get("service.name"),
        endpoint=traces_endpoint,
        sample_ratio=s.otel_traces_sample_ratio,
    )
    _initialized = True


def instrument_fastapi(app: Any) -> None:
    """Wrap a FastAPI app with the auto-instrumentation.

    Must be called AFTER ``init_tracing()``. Safe to call when tracing
    is disabled — instrumenter no-ops if there's no provider.
    """
    if not tracing_enabled():
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)


def instrument_sqlalchemy(engine: Any) -> None:
    """Hook the async SQLAlchemy engine.

    SQLAlchemy auto-instrumentation requires the engine reference;
    can't be done globally like FastAPI's. The api + worker each
    have their own engine instance.
    """
    if not tracing_enabled():
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    # ``engine.sync_engine`` exposes the underlying sync engine that
    # SQLAlchemyInstrumentor knows how to wire — async engines are
    # supported via this attribute since SQLAlchemy 2.0.
    target = getattr(engine, "sync_engine", engine)
    SQLAlchemyInstrumentor().instrument(engine=target)


def instrument_httpx() -> None:
    """Wrap every httpx Client / AsyncClient created in this process."""
    if not tracing_enabled():
        return
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument()


def instrument_redis() -> None:
    """Wrap redis-py / arq's Redis pool with span emission."""
    if not tracing_enabled():
        return
    from opentelemetry.instrumentation.redis import RedisInstrumentor

    RedisInstrumentor().instrument()


def instrument_all_libraries() -> None:
    """Convenience: wire every library-level instrumentor in one call.

    Engine-specific instrumentation (SQLAlchemy + FastAPI) still needs
    explicit hookup at the call site because it requires a reference
    to the constructed object.
    """
    instrument_httpx()
    instrument_redis()
