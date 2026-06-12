"""Delivery assignment state machine.

Flow per the project spec: ``assign → pickup → deliver → POD → complete``.

The transition from DELIVERED → COMPLETED is gated on:
1. POD evidence present (photo / signature / OTP-verified)
2. COD reconciliation done (auto if exact match, supervisor otherwise)

This module enforces (1) at the schema/service layer; (2) is decided by
the COD tolerance config and surfaced as a separate
``reconcile-cod`` admin endpoint when needed.
"""

from __future__ import annotations

from enum import StrEnum


class DeliveryStatus(StrEnum):
    ASSIGNED = "assigned"  # rider has been given the task; not yet picked up
    PICKED_UP = "picked_up"  # rider has the package, en route
    DELIVERED = "delivered"  # handed to recipient with POD; awaiting reconcile
    COMPLETED = "completed"  # POD + (COD reconciled if applicable) — terminal
    CANCELLED = "cancelled"  # admin voided before pickup — terminal
    FAILED = "failed"  # rider returned undelivered — terminal


ALLOWED_TRANSITIONS: dict[DeliveryStatus, frozenset[DeliveryStatus]] = {
    DeliveryStatus.ASSIGNED: frozenset(
        {DeliveryStatus.PICKED_UP, DeliveryStatus.CANCELLED, DeliveryStatus.FAILED},
    ),
    DeliveryStatus.PICKED_UP: frozenset(
        {DeliveryStatus.DELIVERED, DeliveryStatus.FAILED, DeliveryStatus.CANCELLED},
    ),
    DeliveryStatus.DELIVERED: frozenset({DeliveryStatus.COMPLETED}),
    DeliveryStatus.COMPLETED: frozenset(),
    DeliveryStatus.CANCELLED: frozenset(),
    DeliveryStatus.FAILED: frozenset(),
}


class CodReconcileStatus(StrEnum):
    NOT_APPLICABLE = "n/a"  # online payment order — no cash to count
    PENDING = "pending"  # COD order, not yet reconciled
    RECONCILED = "reconciled"  # collected matches expected within tolerance
    DISCREPANCY = "discrepancy"  # mismatch — supervisor must resolve
    RESOLVED = "resolved"  # supervisor closed the discrepancy with a reason


class TransitionError(Exception):
    def __init__(self, current: DeliveryStatus, target: DeliveryStatus) -> None:
        super().__init__(
            f"Cannot transition delivery from {current.value!r} to {target.value!r}",
        )
        self.current = current
        self.target = target


def assert_can_transition(
    current: DeliveryStatus, target: DeliveryStatus,
) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise TransitionError(current, target)
