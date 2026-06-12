"""Rider routing exceptions, mapped to HTTP via the global handler."""

from __future__ import annotations

from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)


class RiderNotFoundError(NotFoundError):
    code = "rider_routing.rider_not_found"


class ShiftNotFoundError(NotFoundError):
    code = "rider_routing.shift_not_found"


class ShiftAlreadyOpenError(ConflictError):
    code = "rider_routing.shift_already_open"


class ShiftClosedError(BusinessRuleError):
    code = "rider_routing.shift_closed"


class StaleLocationError(ValidationError):
    code = "rider_routing.location_stale"


class MockedLocationRejectedError(ValidationError):
    code = "rider_routing.location_mocked"


class RunSheetNotFoundError(NotFoundError):
    code = "rider_routing.run_sheet_not_found"


class RunSheetFrozenError(BusinessRuleError):
    """Raised when a force-next or skip is attempted while a freeze is active."""
    code = "rider_routing.run_sheet_frozen"


class StopNotFoundError(NotFoundError):
    code = "rider_routing.stop_not_found"


class StopNotForRiderError(BusinessRuleError):
    """Raised when a rider tries to mutate a stop on someone else's run sheet."""
    code = "rider_routing.stop_not_for_rider"


class StopNotFeasibleError(BusinessRuleError):
    """Raised when the next-stop engine refuses a stop the caller forced.

    Reasons: prescription not cleared, package not loaded, customer
    unavailable + no override, etc.
    """
    code = "rider_routing.stop_not_feasible"


class NoCandidateStopsError(BusinessRuleError):
    """No stops are eligible for next-stop recommendation right now."""
    code = "rider_routing.no_candidate_stops"


class IllegalStopTransitionError(DomainError):
    code = "rider_routing.illegal_stop_transition"
    status_code = 409
