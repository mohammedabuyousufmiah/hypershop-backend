"""Project-wide Prometheus metrics middleware.

Emits two metrics for every HTTP request the app handles:

  - ``http_requests_total{method, route, status}`` — counter
  - ``http_request_duration_seconds{method, route, status}`` — histogram

Cardinality control:
  The ``route`` label is the **route template** (e.g.
  ``/api/v1/products/{product_id}/videos``), NOT the raw path. We read
  ``request.scope["route"].path`` AFTER ``call_next()`` returns —
  Starlette populates ``scope["route"]`` once routing has matched, so
  we get the templated path with placeholders intact. Without this,
  every UUID in a path would create a unique label value and Prometheus
  storage would explode.

  For routes that did NOT match (404), we use the literal label
  ``"<unmatched>"`` so unbounded crawler / probe traffic against
  random URLs collapses into a single time series.

Why ``BaseHTTPMiddleware`` (not pure ASGI):
  - Mirrors the pattern already in use across the project
    (``AccessLogMiddleware``, ``SecurityHeadersMiddleware``).
  - ``call_next()`` returns AFTER routing has matched, which is the
    only point we can read the route template safely. Pure ASGI
    middleware that wraps the entire app sees scope BEFORE routing.

Bucket choice:
  Default Prometheus boundaries (5ms..10s + Inf) cover the expected
  Hypershop API response distribution. Tune at the operator side via
  histogram_quantile recording rules — don't try to make one bucket
  set fit every endpoint.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.metrics import make_counter, make_histogram

# Module-level definitions register against the project registry on
# import. Tests that need clean state should rebuild the app via
# ``create_app()`` and reset the registry through
# ``app.core.metrics.reset_registry()`` BEFORE re-importing this file.
_http_requests_total = make_counter(
    "http_requests_total",
    "Total HTTP requests handled by the api process. Labeled by "
    "method, route TEMPLATE (not raw path — UUID safety), and status. "
    "404s for unmatched paths land under route='<unmatched>' so "
    "crawler probes don't blow up label cardinality.",
    labels=["method", "route", "status"],
)

_http_request_duration_seconds = make_histogram(
    "http_request_duration_seconds",
    "End-to-end HTTP request handling time in seconds (from middleware "
    "entry to response start). Includes routing, dependencies, handler, "
    "and serialisation; excludes time spent in upstream proxies.",
    labels=["method", "route", "status"],
    buckets=(
        0.005, 0.01, 0.025, 0.05, 0.1,
        0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
        float("inf"),
    ),
)


class PrometheusMetricsMiddleware(BaseHTTPMiddleware):
    """Records request count + duration histogram for every HTTP request.

    Position this middleware OUTERMOST in the chain so its timing
    captures the full lifecycle (including the work other middleware
    does in their dispatch). In ``main.py``, that means added LAST
    via ``app.add_middleware`` — Starlette wraps in reverse-add order,
    so last-added is outermost.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        method = request.method
        started = time.perf_counter()
        status_code: int = 500  # default if handler raises before producing a response

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            # Re-raise after observing — we still want the metric for
            # crashed-handler endpoints so dashboards see a 5xx spike.
            raise
        finally:
            duration = time.perf_counter() - started
            route_obj = request.scope.get("route")
            route_path = (
                getattr(route_obj, "path", None) or "<unmatched>"
            )
            labels = {
                "method": method,
                "route": route_path,
                "status": str(status_code),
            }
            _http_requests_total.labels(**labels).inc()
            _http_request_duration_seconds.labels(**labels).observe(duration)
