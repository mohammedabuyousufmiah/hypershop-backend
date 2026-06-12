from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import (
    ConflictError,
    DomainError,
    ServiceUnavailableError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.validation import ErrorEnvelope

_logger = get_logger("hypershop.errors")


def _envelope(
    request: Request,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Standard error envelope.

    Mirrors the success envelope from `ResponseEnvelopeMiddleware` but
    error data lives under `error` (XOR with `data` — never both):

      {
        "success": false,
        "error": {
          "code": "<machine code>",
          "message": "<human-readable>",
          "details": {...}
        },
        "meta": { "request_id": "<uuid>", "pagination": {} }
      }

    Migration: top-level `message` and `data` keys are GONE on errors.
    The FE api-client `toApiError` handles all 3 shapes (new `error`
    envelope, transitional `data` shape, legacy flat `{code, detail}`)
    so existing FE code keeps working through the rollout.
    """
    request_id = getattr(request.state, "request_id", None)
    # Keep ErrorEnvelope around for telemetry / log structuring even
    # though the wire format moved.
    _ = ErrorEnvelope(
        code=code,
        message=message,
        details=details or {},
        request_id=request_id,
    )
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


def _safe_message(exc: Exception, fallback: str) -> str:
    msg = str(exc).strip()
    return msg if msg and len(msg) < 500 else fallback


async def _handle_domain(request: Request, exc: DomainError) -> JSONResponse:
    if exc.status_code >= 500:
        _logger.error("domain_error", code=exc.code, message=exc.message, details=exc.details)
    else:
        _logger.info("domain_error", code=exc.code, message=exc.message, details=exc.details)
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(request, exc.code, exc.message, exc.details),
    )


async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_envelope(
            request,
            ValidationError.code,
            "Request payload failed validation.",
            # exc.errors() can embed non-serialisable objects (e.g. a raw
            # ValueError in ctx when a pydantic validator raises) → json.dumps
            # would crash the handler itself (500). default=str makes it safe.
            {"errors": json.loads(json.dumps(exc.errors(), default=str))},
        ),
    )


async def _handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(
            request,
            f"http_{exc.status_code}",
            _safe_message(exc, "HTTP error."),
        ),
    )


async def _handle_integrity(request: Request, exc: IntegrityError) -> JSONResponse:
    _logger.warning("db_integrity_error", error=_safe_message(exc, "constraint violation"))
    return JSONResponse(
        status_code=ConflictError.status_code,
        content=_envelope(
            request,
            ConflictError.code,
            "Database constraint violated.",
        ),
    )


async def _handle_operational(request: Request, exc: OperationalError) -> JSONResponse:
    _logger.error("db_operational_error", error=_safe_message(exc, "db operational failure"))
    return JSONResponse(
        status_code=ServiceUnavailableError.status_code,
        content=_envelope(
            request,
            ServiceUnavailableError.code,
            "Database temporarily unavailable.",
        ),
    )


async def _handle_sqlalchemy(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    _logger.exception("db_unhandled_error", error=_safe_message(exc, "db error"))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_envelope(request, "internal_error", "Database error."),
    )


async def _handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
    _logger.exception("unhandled_error", error=_safe_message(exc, "unhandled exception"))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_envelope(request, "internal_error", "Internal server error."),
    )


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _handle_domain)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _handle_http)  # type: ignore[arg-type]
    app.add_exception_handler(IntegrityError, _handle_integrity)  # type: ignore[arg-type]
    app.add_exception_handler(OperationalError, _handle_operational)  # type: ignore[arg-type]
    app.add_exception_handler(SQLAlchemyError, _handle_sqlalchemy)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unhandled)
