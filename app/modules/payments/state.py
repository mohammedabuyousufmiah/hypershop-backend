"""Payment lifecycle states.

A PaymentIntent is created the moment a customer initiates payment for
an order. It walks the following state machine:

  initiated  ─►  authorized  ─►  captured  ─►  refunded
       │              │
       └──────────────┴──►  failed   /   cancelled  /  expired

States are kept as strings (not enums) for cross-language fidelity with
gateway webhooks; the CHECK constraint on the DB column is the source
of truth.
"""

from __future__ import annotations

from enum import StrEnum


class PaymentStatus(StrEnum):
    INITIATED = "initiated"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class PaymentAttemptStatus(StrEnum):
    """Per-call outcome — used for retry/observability, not auth state."""

    OK = "ok"
    FAILED = "failed"
    TIMEOUT = "timeout"


class WebhookStatus(StrEnum):
    RECEIVED = "received"
    PROCESSED = "processed"
    REJECTED = "rejected"  # signature failed or unknown payment
    DUPLICATE = "duplicate"  # idempotency hit


class RefundStatus(StrEnum):
    REQUESTED = "requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# Terminal states a PaymentIntent can never leave.
TERMINAL_PAYMENT_STATES: frozenset[str] = frozenset({
    PaymentStatus.CAPTURED,
    PaymentStatus.FAILED,
    PaymentStatus.CANCELLED,
    PaymentStatus.EXPIRED,
    PaymentStatus.REFUNDED,
})

# States that mean money has actually moved (used by reconciliation).
MONEY_MOVED_STATES: frozenset[str] = frozenset({
    PaymentStatus.CAPTURED,
    PaymentStatus.PARTIALLY_REFUNDED,
    PaymentStatus.REFUNDED,
})


def is_terminal(status: str) -> bool:
    return status in TERMINAL_PAYMENT_STATES


_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    PaymentStatus.INITIATED: frozenset({
        PaymentStatus.AUTHORIZED,
        PaymentStatus.CAPTURED,  # one-shot gateways skip authorize
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
    }),
    PaymentStatus.AUTHORIZED: frozenset({
        PaymentStatus.CAPTURED,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
    }),
    PaymentStatus.CAPTURED: frozenset({
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.REFUNDED,
    }),
    PaymentStatus.PARTIALLY_REFUNDED: frozenset({
        PaymentStatus.REFUNDED,
    }),
}


def can_transition(from_state: str, to_state: str) -> bool:
    return to_state in _ALLOWED_TRANSITIONS.get(from_state, frozenset())
