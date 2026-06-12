"""Top-level middleware namespace.

Canonical import path for ASGI middleware classes. Re-exports from
`app.core.middleware` so legacy imports continue to work — new code
should prefer `app.middleware.*`.
"""
from app.core.middleware import (
    AccessLogMiddleware,
    PrometheusMetricsMiddleware,
    RequestIdMiddleware,
    ResponseEnvelopeMiddleware,
    SecurityHeadersMiddleware,
)

__all__ = [
    "AccessLogMiddleware",
    "PrometheusMetricsMiddleware",
    "RequestIdMiddleware",
    "ResponseEnvelopeMiddleware",
    "SecurityHeadersMiddleware",
]
