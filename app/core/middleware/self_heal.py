"""Self-heal middleware — server-side half of the resilience engine.

Sits OUTERMOST in the middleware stack. For every request it:

  1. Retries transient infrastructure errors (DB connection dropped,
     "server closed the connection", asyncpg disconnect) a couple of
     times with short backoff — these are the flaps that otherwise
     surface as random 500s under load.
  2. Catches any unhandled exception so a single bad request can never
     propagate as an opaque crash; returns a graceful, *recoverable*
     error envelope the frontend resilience layer knows to retry.
  3. Records recent incidents in a ring buffer exposed at
     /api/v1/admin/resilience/status for observability.

It does NOT mask real bugs — the original exception is logged in full.
It makes failures recoverable instead of fatal.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.logging import get_logger

_logger = get_logger("hypershop.self_heal")

# Recent incidents (most-recent-last). Bounded so it never grows.
INCIDENTS: Deque[dict[str, Any]] = deque(maxlen=100)
_STATS = {"requests": 0, "retried": 0, "recovered": 0, "failed": 0}

# Exception types / messages treated as transient → safe to retry.
_TRANSIENT_HINTS = (
    "server closed the connection",
    "connection was closed",
    "connection is closed",
    "connection reset",
    "terminating connection",
    "cannot perform operation",
    "operationalerror",
    "interfaceerror",
    "timeout",
    "temporarily unavailable",
)


def _is_transient(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    if name in ("operationalerror", "interfaceerror", "disconnectionerror",
                "timeouterror", "connectionerror"):
        return True
    msg = str(exc).lower()
    return any(h in msg for h in _TRANSIENT_HINTS)


def _record(path: str, exc: BaseException, outcome: str) -> None:
    INCIDENTS.append({
        "ts": int(time.time()),
        "path": path,
        "error_type": type(exc).__name__,
        "error": str(exc)[:300],
        "outcome": outcome,
    })


class SelfHealMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, max_retries: int = 2, base_delay: float = 0.25):
        super().__init__(app)
        self.max_retries = max_retries
        self.base_delay = base_delay

    async def dispatch(self, request: Request, call_next) -> Response:
        _STATS["requests"] += 1
        path = request.url.path
        attempt = 0
        last_exc: BaseException | None = None

        while attempt <= self.max_retries:
            try:
                resp = await call_next(request)
                if attempt > 0:
                    _STATS["recovered"] += 1
                    _record(path, last_exc or RuntimeError("retry"), "recovered")
                    _logger.info(
                        "self_heal_recovered", extra={"path": path, "attempt": attempt})
                return resp
            except Exception as exc:  # noqa: BLE001 — deliberate catch-all
                last_exc = exc
                transient = _is_transient(exc)
                # Only retry idempotent-ish methods on transient infra errors.
                retryable = transient and request.method in ("GET", "HEAD", "OPTIONS")
                if retryable and attempt < self.max_retries:
                    _STATS["retried"] += 1
                    delay = self.base_delay * (2 ** attempt)
                    _logger.warning(
                        "self_heal_retry",
                        extra={"path": path, "attempt": attempt, "error": str(exc)[:200]})
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                break

        # Exhausted / non-retryable → graceful recoverable envelope.
        _STATS["failed"] += 1
        _record(path, last_exc or RuntimeError("unknown"), "degraded")
        _logger.error(
            "self_heal_degraded",
            extra={"path": path, "error": str(last_exc)[:300]},
            exc_info=last_exc,
        )
        rid = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "error": {
                    "code": "temporarily_unavailable",
                    "message": "A transient problem occurred. Please retry.",
                    "details": {},
                },
                "meta": {"request_id": rid, "pagination": {}, "recoverable": True},
            },
            headers={"Retry-After": "1"},
        )
