from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base for every error that maps to a 4xx/5xx HTTP response.

    Subclasses must set ``code`` (machine-readable) and ``status_code``.
    Extra context goes in ``details`` and is surfaced to clients verbatim, so
    callers must NEVER put PII or internal stack info in there.
    """

    code: str = "internal_error"
    status_code: int = 500
    public_message: str = "Internal server error."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.public_message)
        self.message = message or self.public_message
        self.details: dict[str, Any] = details or {}


class ValidationError(DomainError):
    code = "validation_error"
    status_code = 422
    public_message = "Request failed validation."


class NotFoundError(DomainError):
    code = "not_found"
    status_code = 404
    public_message = "Resource not found."


class ConflictError(DomainError):
    code = "conflict"
    status_code = 409
    public_message = "Conflict with current resource state."


class UnauthenticatedError(DomainError):
    code = "unauthenticated"
    status_code = 401
    public_message = "Authentication required."


class ForbiddenError(DomainError):
    code = "forbidden"
    status_code = 403
    public_message = "You are not allowed to perform this action."


class RateLimitedError(DomainError):
    code = "rate_limited"
    status_code = 429
    public_message = "Too many requests. Slow down."


class IdempotencyConflictError(DomainError):
    code = "idempotency_conflict"
    status_code = 409
    public_message = "Idempotency-Key was reused with a different request body."


class ServiceUnavailableError(DomainError):
    code = "service_unavailable"
    status_code = 503
    public_message = "Service temporarily unavailable."


class IntegrationError(DomainError):
    code = "integration_error"
    status_code = 502
    public_message = "Upstream provider returned an error."


class BusinessRuleError(DomainError):
    code = "business_rule_violation"
    status_code = 422
    public_message = "Operation violates a business rule."
