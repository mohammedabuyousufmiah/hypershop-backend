"""Observability: Sentry + Prometheus + OpenTelemetry + request-id middleware.

All providers are optional and silently no-op if their env config is absent.
"""
from __future__ import annotations

import logging
import re
import time

from fastapi import FastAPI, Request
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.config import settings
from app.logging_setup import new_request_id, request_id_ctx
from app.pii import redact_path

# Recognises UUID + long hex/digit ids and replaces them with `:id` to keep
# Prometheus label cardinality bounded.
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_LONG_ID_RE = re.compile(r"^[0-9a-fA-F]{12,}$|^\d{4,}$")

logger = logging.getLogger(__name__)

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path_bucket", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration (seconds)",
    ["method", "path_bucket"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
INFLIGHT = Gauge("http_requests_inflight", "In-flight HTTP requests")
WHATSAPP_WEBHOOK = Counter(
    "whatsapp_webhook_total",
    "WhatsApp webhook events",
    ["outcome"],
)
DLQ_WRITTEN = Counter("dlq_written_total", "Messages written to DLQ", ["source"])


def _path_bucket(path: str) -> str:
    """Collapse UUIDs / long ids into `:id` and trim depth to keep Prometheus
    cardinality bounded — otherwise every distinct UUID becomes its own label
    value and the metrics database explodes."""
    parts: list[str] = []
    for raw in path.split("/")[:5]:
        if not raw:
            continue
        if _UUID_RE.match(raw) or _LONG_ID_RE.match(raw):
            parts.append(":id")
        else:
            parts.append(raw)
    return "/" + "/".join(parts) if parts else "/"


class RequestIdAndMetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or new_request_id()
        token = request_id_ctx.set(rid)
        # _path_bucket itself collapses UUIDs/digits → :id, so we don't need
        # redact_path here (phone regex would otherwise break UUID structure
        # before bucket detection runs).
        bucket = _path_bucket(request.url.path)
        INFLIGHT.inc()
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            duration = time.perf_counter() - start
            INFLIGHT.dec()
            REQUEST_LATENCY.labels(request.method, bucket).observe(duration)
            REQUEST_COUNT.labels(request.method, bucket, str(status_code)).inc()
            request_id_ctx.reset(token)


def setup_sentry() -> None:
    cfg = settings()
    if not cfg.sentry_dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=cfg.sentry_dsn,
            traces_sample_rate=cfg.sentry_traces_sample_rate,
            environment=cfg.app_env,
            send_default_pii=False,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        )
        logger.info("sentry_initialised env=%s", cfg.app_env)
    except Exception:  # pragma: no cover
        logger.exception("sentry_init_failed")


def setup_otel(app: FastAPI) -> None:
    cfg = settings()
    if not cfg.otel_exporter_otlp_endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource(attributes={SERVICE_NAME: cfg.otel_service_name or "customer-care-pwa"})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=cfg.otel_exporter_otlp_endpoint))
        )
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        SQLAlchemyInstrumentor().instrument()
        logger.info("otel_initialised endpoint=%s", cfg.otel_exporter_otlp_endpoint)
    except Exception:  # pragma: no cover
        logger.exception("otel_init_failed")


def metrics_endpoint() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
