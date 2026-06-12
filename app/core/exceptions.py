"""Canonical exception module.

Re-exports the existing `app/core/errors.py` classes under the more
conventional name. Both paths work — new code should prefer importing
from `app.core.exceptions`; legacy imports from `app.core.errors`
remain valid.

  from app.core.exceptions import DomainError, NotFoundError, ConflictError

Error codes raised here flow through `app/core/exception_handlers.py`
into the standard error envelope:

  { "success": false,
    "error": { "code": <class.code>, "message": <str(exc)>, "details": {...} },
    "meta":  { "request_id": "...", "pagination": {} } }
"""
from __future__ import annotations

from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    DomainError,
    ForbiddenError,
    IdempotencyConflictError,
    IntegrationError,
    NotFoundError,
    RateLimitedError,
    ServiceUnavailableError,
    UnauthenticatedError,
    ValidationError,
)

__all__ = [
    "BusinessRuleError",
    "ConflictError",
    "DomainError",
    "ForbiddenError",
    "IdempotencyConflictError",
    "IntegrationError",
    "NotFoundError",
    "RateLimitedError",
    "ServiceUnavailableError",
    "UnauthenticatedError",
    "ValidationError",
]
