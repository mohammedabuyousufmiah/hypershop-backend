"""Standard response envelope helpers.

`ResponseEnvelopeMiddleware` (in `app/core/middleware/response_envelope.py`)
wraps every 2xx JSON response automatically — most routes don't need
to call anything in this file. Use these helpers when a route wants
EXPLICIT control:

  * custom `message` (not the default "OK")
  * pre-built `meta.pagination` (when the route knows the page math
    better than the middleware's key-sniffing)
  * raw `JSONResponse` with the envelope (for routes that bypass the
    auto-wrap path because they emit non-JSON or set custom headers)

Shape contract — keep in sync with
``app/core/middleware/response_envelope.py`` and ``docs/API_CONTRACT_2026-05-16.md``::

    Success: { success:true,  message, data,  meta: {request_id, pagination} }
    Error:   { success:false,                error: {code, message, details},
                                              meta: {request_id, pagination} }

`data` and `error` are XOR: success carries `data`, error carries `error`.
"""
from __future__ import annotations

from typing import Any

from fastapi import Request, status
from fastapi.responses import JSONResponse


# ─── Builders (plain dict — for routes that return the dict and let
# FastAPI serialise it / let the middleware add meta if it's missing) ──

def success_envelope(
    data: Any,
    *,
    message: str = "OK",
    request_id: str | None = None,
    pagination: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the standard 2xx envelope as a plain dict.

    Pass to `JSONResponse(content=...)` or just `return` it from a
    route. If middleware later sees this already-enveloped shape, it
    leaves it alone (double-wrap guard).
    """
    return {
        "success": True,
        "message": message,
        "data": data,
        "meta": {
            "request_id": request_id,
            "pagination": pagination or {},
        },
    }


def error_envelope(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build the standard 4xx/5xx envelope as a plain dict.

    Most routes raise a `DomainError` and let `exception_handlers.py`
    produce this. Use this builder only when emitting a custom error
    response that bypasses the exception machinery.
    """
    return {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
        "meta": {
            "request_id": request_id,
            "pagination": {},
        },
    }


def paginated_envelope(
    items: list[Any],
    *,
    total: int,
    offset: int = 0,
    limit: int = 50,
    message: str = "OK",
    request_id: str | None = None,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convenience builder for paginated list responses.

    `data` always has `items + total + offset + limit`. `meta.pagination`
    mirrors the page counters so consumers can read them from either
    place (FE pattern is `body.meta.pagination` for nav state, `body.data.items`
    for rendering).
    """
    payload = {"items": items, "total": total, "offset": offset, "limit": limit}
    if extras:
        payload.update(extras)
    return success_envelope(
        payload,
        message=message,
        request_id=request_id,
        pagination={"total": total, "offset": offset, "limit": limit},
    )


# ─── JSONResponse wrappers (for routes that need to set custom status
# codes or headers alongside the envelope) ─────────────────────────────

def success_response(
    request: Request,
    data: Any,
    *,
    status_code: int = status.HTTP_200_OK,
    message: str = "OK",
    pagination: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """`JSONResponse` carrying the standard success envelope."""
    return JSONResponse(
        status_code=status_code,
        content=success_envelope(
            data,
            message=message,
            request_id=getattr(request.state, "request_id", None),
            pagination=pagination,
        ),
        headers=headers,
    )


def error_response(
    request: Request,
    code: str,
    message: str,
    *,
    status_code: int = status.HTTP_400_BAD_REQUEST,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """`JSONResponse` carrying the standard error envelope."""
    return JSONResponse(
        status_code=status_code,
        content=error_envelope(
            code,
            message,
            details=details,
            request_id=getattr(request.state, "request_id", None),
        ),
        headers=headers,
    )


def paginated_response(
    request: Request,
    items: list[Any],
    *,
    total: int,
    offset: int = 0,
    limit: int = 50,
    status_code: int = status.HTTP_200_OK,
    message: str = "OK",
    extras: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """`JSONResponse` carrying the standard paginated envelope."""
    return JSONResponse(
        status_code=status_code,
        content=paginated_envelope(
            items,
            total=total,
            offset=offset,
            limit=limit,
            message=message,
            request_id=getattr(request.state, "request_id", None),
            extras=extras,
        ),
        headers=headers,
    )


__all__ = [
    "success_envelope",
    "error_envelope",
    "paginated_envelope",
    "success_response",
    "error_response",
    "paginated_response",
]
