"""Project-wide Prometheus metrics registry + ``/metrics`` exporter.

This module is the **single source of truth** for the registry that
backs the ``GET /metrics`` endpoint. Modules that need to emit metrics
register their Counter/Gauge/Histogram instances against the registry
exposed here (see ``REGISTRY`` and the ``register_collector()`` helper),
and the exporter scrapes everything in one shot.

Why a project-owned registry instead of `prometheus_client.REGISTRY`
(the library default):
- The library's default registry is global module state, which makes
  test isolation painful (counters survive between tests, mock-fixture
  state leaks).
- Owning our own registry lets us build a fresh one per FastAPI app
  factory call (``create_app()``), so integration tests get a clean
  metric surface every run.

Multi-process note (gunicorn 4-worker prod):
- prometheus-client supports multiprocess mode via the
  ``PROMETHEUS_MULTIPROC_DIR`` env var + ``MultiProcessCollector``.
- We DO NOT enable that here — single-process registry is fine for
  the worker container (1 process) and acceptable-but-imperfect for
  the api container (4 workers, scrape will see only the worker that
  served the request).
- For prod sum-across-workers accuracy, follow the multiproc setup
  in ``docs/MONITORING_MODULE_35.md`` Section 7. Until then, prefer
  HISTOGRAMs (which have natural per-process aggregation via
  buckets) over counters where exact totals matter.

Endpoint protection:
- ``/metrics`` is mounted at the api root (no ``/api/v1`` prefix —
  Prometheus scrapers expect the raw path).
- We DO NOT add IP allowlist or basic auth here. Production network
  isolation (Caddy not proxying ``/metrics``, k8s NetworkPolicy)
  is the deployment-time control. If your deployment exposes
  ``/metrics`` to the public internet, that's a deployment bug — not
  this module's job to fix.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    generate_latest,
)
from starlette.requests import Request
from starlette.responses import Response

# ---------- registry ----------

# Singleton registry. Created lazily so import order doesn't matter
# (tests can call ``reset_registry()`` between cases).
_registry: CollectorRegistry | None = None


def get_registry() -> CollectorRegistry:
    """Return the project's Prometheus registry, creating it on first call."""
    global _registry
    if _registry is None:
        _registry = CollectorRegistry(auto_describe=True)
    return _registry


def reset_registry() -> None:
    """Drop + recreate the registry. Test-only; do NOT call in production."""
    global _registry
    _registry = CollectorRegistry(auto_describe=True)


def register_collector(collector: Any) -> None:
    """Register a custom collector class (subclass of ``Collector``).

    Used for DB-derived gauges that compute on every scrape — see
    :class:`app.modules.product_videos.metrics.PipelineStateCollector`
    for the canonical pattern.
    """
    get_registry().register(collector)


# ---------- ASGI handler ----------

async def metrics_endpoint(_request: Request) -> Response:
    """ASGI handler for ``GET /metrics``.

    Renders the registry in the standard Prometheus text exposition
    format. The response Content-Type is set per spec
    (``text/plain; version=0.0.4; charset=utf-8``) so Prometheus
    scrapers parse correctly.
    """
    payload = generate_latest(get_registry())
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)


def install_metrics_route(app: Any) -> None:
    """Wire ``GET /metrics`` onto a FastAPI app.

    Called by ``create_app()`` once. Idempotent in the sense that
    re-calling overwrites the existing route — but FastAPI will warn
    about duplicate paths, so callers should call this once.
    """
    app.add_route("/metrics", metrics_endpoint, methods=["GET"], include_in_schema=False)


# ---------- module-side helpers ----------

def _kwargs_with_registry(extra: dict[str, Any]) -> dict[str, Any]:
    return {"registry": get_registry(), **extra}


def make_counter(name: str, doc: str, labels: list[str] | None = None) -> Any:
    """Convenience: ``Counter(name, doc, labelnames=..., registry=...)``."""
    from prometheus_client import Counter

    return Counter(
        name,
        doc,
        labelnames=labels or [],
        **_kwargs_with_registry({}),
    )


def make_gauge(name: str, doc: str, labels: list[str] | None = None) -> Any:
    from prometheus_client import Gauge

    return Gauge(
        name,
        doc,
        labelnames=labels or [],
        **_kwargs_with_registry({}),
    )


def make_histogram(
    name: str,
    doc: str,
    labels: list[str] | None = None,
    buckets: tuple[float, ...] | None = None,
) -> Any:
    from prometheus_client import Histogram

    extra: dict[str, Any] = {}
    if buckets is not None:
        extra["buckets"] = buckets
    return Histogram(
        name,
        doc,
        labelnames=labels or [],
        **_kwargs_with_registry(extra),
    )


# ---------- collector base ----------

# Re-exported so module-side metric files don't need to import
# prometheus_client directly when defining custom collectors. Keeps
# the module-side surface minimal: import from app.core.metrics only.

from prometheus_client.core import (  # noqa: E402 — re-export at bottom
    GaugeMetricFamily,
    CounterMetricFamily,
)

__all__ = [
    "CounterMetricFamily",
    "GaugeMetricFamily",
    "get_registry",
    "install_metrics_route",
    "make_counter",
    "make_gauge",
    "make_histogram",
    "metrics_endpoint",
    "register_collector",
    "reset_registry",
]


# Type-only export so module-side files can declare ``Collector``
# subclasses without prometheus_client in their import surface.
class CollectorBase:
    """Base for custom collectors. Subclass and implement ``collect()``.

    Example::

        class MyCollector(CollectorBase):
            def collect(self) -> Iterable[GaugeMetricFamily]:
                yield GaugeMetricFamily("my_gauge", "doc", value=42.0)

        register_collector(MyCollector())

    See :mod:`app.modules.product_videos.metrics` for a real-world
    DB-driven collector that runs SQL on every scrape.
    """

    def collect(self):  # pragma: no cover — override required
        raise NotImplementedError(
            "Subclass CollectorBase and implement collect()",
        )


def _module_callable(fn: Callable[..., Any]) -> Callable[..., Any]:
    """No-op decorator placeholder; reserved for future module hooks."""
    return fn
