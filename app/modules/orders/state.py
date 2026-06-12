"""Order state machine — the single source of truth for transitions.

Hypershop is a pure e-commerce marketplace; there is no pharmacy /
prescription gate in the order flow. Every paid order goes straight
to APPROVED once stock is reserved.

The flow:

    PENDING_PAYMENT → PAYMENT_CONFIRMED → STOCK_RESERVED →
        APPROVED → PACKING → OUT_FOR_DELIVERY → COMPLETED

Every transition is also a row in ``order_status_history``. Both the service
layer and the DB CHECK on ``orders.status`` validate that we never land in
an unknown state.
"""

from __future__ import annotations

from enum import StrEnum


class OrderStatus(StrEnum):
    PENDING_PAYMENT = "pending_payment"  # online checkout, awaiting gateway
    PAYMENT_CONFIRMED = "payment_confirmed"  # gateway settled / COD committed
    STOCK_RESERVED = "stock_reserved"  # inventory locked the units (FEFO)
    APPROVED = "approved"  # cleared to fulfil
    PACKING = "packing"
    OUT_FOR_DELIVERY = "out_for_delivery"
    COMPLETED = "completed"  # customer received it
    CANCELLED = "cancelled"  # terminal — voluntary or admin
    FAILED = "failed"  # terminal — system-side failure (e.g. insufficient stock)


# What states a given state is allowed to move to. ``frozenset()`` = terminal.
ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING_PAYMENT: frozenset(
        {OrderStatus.PAYMENT_CONFIRMED, OrderStatus.CANCELLED, OrderStatus.FAILED},
    ),
    OrderStatus.PAYMENT_CONFIRMED: frozenset(
        {OrderStatus.STOCK_RESERVED, OrderStatus.FAILED, OrderStatus.CANCELLED},
    ),
    OrderStatus.STOCK_RESERVED: frozenset(
        {OrderStatus.APPROVED, OrderStatus.CANCELLED},
    ),
    OrderStatus.APPROVED: frozenset({OrderStatus.PACKING, OrderStatus.CANCELLED}),
    OrderStatus.PACKING: frozenset(
        {OrderStatus.OUT_FOR_DELIVERY, OrderStatus.CANCELLED},
    ),
    OrderStatus.OUT_FOR_DELIVERY: frozenset(
        {OrderStatus.COMPLETED, OrderStatus.CANCELLED},
    ),
    OrderStatus.COMPLETED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.FAILED: frozenset({OrderStatus.CANCELLED}),
}


# States in which the customer themself is allowed to cancel. Beyond PACKING,
# admin/staff override is required (refund / pickup / etc).
CUSTOMER_CANCELLABLE_STATES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.PENDING_PAYMENT,
        OrderStatus.PAYMENT_CONFIRMED,
        OrderStatus.STOCK_RESERVED,
        OrderStatus.APPROVED,
    },
)


# Terminal states — once here, no further transitions allowed (except FAILED
# which can be admin-cancelled to clear it from active queues).
TERMINAL_STATES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.COMPLETED, OrderStatus.CANCELLED},
)


class TransitionError(Exception):
    """Raised when a transition is not in ``ALLOWED_TRANSITIONS`` for the
    current state. The service layer wraps this in a ``BusinessRuleError``
    for the API response.
    """

    def __init__(self, current: OrderStatus, target: OrderStatus) -> None:
        super().__init__(
            f"Cannot transition from {current.value!r} to {target.value!r}",
        )
        self.current = current
        self.target = target


def assert_can_transition(current: OrderStatus, target: OrderStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise TransitionError(current, target)


def is_terminal(status: OrderStatus) -> bool:
    return status in TERMINAL_STATES


# ---------------------------------------------------------------------------
# 21-state fulfillment sub-status machine.
#
# Lives in parallel to OrderStatus (the legal/financial truth). This machine
# is consumed by the ops/rider/hub UI to model the detailed last-mile flow
# without disrupting checkout/payment/inventory callers wired on OrderStatus.
#
# Persisted on ``orders.fulfillment_stage`` (added by migration 0080).
# Transitions are logged into ``order_fulfillment_stage_history``.
# ---------------------------------------------------------------------------


class OrderFulfillmentStage(StrEnum):
    ORDER_PLACED = "ORDER_PLACED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_VERIFIED = "PAYMENT_VERIFIED"
    COD_ACCEPTED = "COD_ACCEPTED"
    SELLER_PROCESSING = "SELLER_PROCESSING"
    READY_TO_SHIP = "READY_TO_SHIP"
    PICKUP_ASSIGNED = "PICKUP_ASSIGNED"
    PICKED_UP = "PICKED_UP"
    AT_HUB = "AT_HUB"
    SORTED_FOR_DELIVERY = "SORTED_FOR_DELIVERY"
    RIDER_ASSIGNED = "RIDER_ASSIGNED"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERY_ATTEMPTED = "DELIVERY_ATTEMPTED"
    DELIVERED = "DELIVERED"
    FAILED_DELIVERY = "FAILED_DELIVERY"
    RESCHEDULED = "RESCHEDULED"
    RETURNING_TO_HUB = "RETURNING_TO_HUB"
    RETURNED_TO_HUB = "RETURNED_TO_HUB"
    RETURNING_TO_SELLER = "RETURNING_TO_SELLER"
    RETURNED_TO_SELLER = "RETURNED_TO_SELLER"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


# Cancellation/refund are reachable from almost any non-terminal stage; the
# allowed-out set below lists the *normal forward* edges. The service helper
# layers CANCELLED + REFUNDED on top via :func:`stage_can_cancel`.
_FS = OrderFulfillmentStage
STAGE_TRANSITIONS: dict[OrderFulfillmentStage, frozenset[OrderFulfillmentStage]] = {
    _FS.ORDER_PLACED: frozenset({_FS.PAYMENT_PENDING, _FS.COD_ACCEPTED}),
    _FS.PAYMENT_PENDING: frozenset({_FS.PAYMENT_VERIFIED}),
    _FS.PAYMENT_VERIFIED: frozenset({_FS.SELLER_PROCESSING}),
    _FS.COD_ACCEPTED: frozenset({_FS.SELLER_PROCESSING}),
    _FS.SELLER_PROCESSING: frozenset({_FS.READY_TO_SHIP}),
    _FS.READY_TO_SHIP: frozenset({_FS.PICKUP_ASSIGNED}),
    _FS.PICKUP_ASSIGNED: frozenset({_FS.PICKED_UP}),
    _FS.PICKED_UP: frozenset({_FS.AT_HUB}),
    _FS.AT_HUB: frozenset({_FS.SORTED_FOR_DELIVERY}),
    _FS.SORTED_FOR_DELIVERY: frozenset({_FS.RIDER_ASSIGNED}),
    _FS.RIDER_ASSIGNED: frozenset({_FS.OUT_FOR_DELIVERY}),
    _FS.OUT_FOR_DELIVERY: frozenset({_FS.DELIVERY_ATTEMPTED, _FS.DELIVERED}),
    _FS.DELIVERY_ATTEMPTED: frozenset({_FS.DELIVERED, _FS.FAILED_DELIVERY}),
    _FS.FAILED_DELIVERY: frozenset({_FS.RESCHEDULED, _FS.RETURNING_TO_HUB}),
    _FS.RESCHEDULED: frozenset({_FS.OUT_FOR_DELIVERY}),
    _FS.RETURNING_TO_HUB: frozenset({_FS.RETURNED_TO_HUB}),
    _FS.RETURNED_TO_HUB: frozenset({_FS.RETURNING_TO_SELLER, _FS.RIDER_ASSIGNED}),
    _FS.RETURNING_TO_SELLER: frozenset({_FS.RETURNED_TO_SELLER}),
    _FS.RETURNED_TO_SELLER: frozenset({_FS.REFUNDED}),
    _FS.DELIVERED: frozenset({_FS.RETURNING_TO_HUB}),  # post-delivery RMA loop
    _FS.CANCELLED: frozenset({_FS.REFUNDED}),
    _FS.REFUNDED: frozenset(),
}


# Stages from which a CANCELLED transition is allowed (anything pre-dispatch
# + FAILED_DELIVERY). Post-dispatch cancels must go through the return leg.
STAGE_CANCELLABLE: frozenset[OrderFulfillmentStage] = frozenset(
    {
        _FS.ORDER_PLACED,
        _FS.PAYMENT_PENDING,
        _FS.PAYMENT_VERIFIED,
        _FS.COD_ACCEPTED,
        _FS.SELLER_PROCESSING,
        _FS.READY_TO_SHIP,
        _FS.PICKUP_ASSIGNED,
        _FS.FAILED_DELIVERY,
    },
)


STAGE_TERMINAL: frozenset[OrderFulfillmentStage] = frozenset(
    {_FS.DELIVERED, _FS.CANCELLED, _FS.REFUNDED, _FS.RETURNED_TO_SELLER},
)


class StageTransitionError(Exception):
    """Raised when a stage transition violates STAGE_TRANSITIONS."""

    def __init__(
        self,
        current: OrderFulfillmentStage,
        target: OrderFulfillmentStage,
    ) -> None:
        super().__init__(
            f"Cannot transition stage from {current.value!r} to {target.value!r}",
        )
        self.current = current
        self.target = target


def assert_stage_transition(
    current: OrderFulfillmentStage,
    target: OrderFulfillmentStage,
) -> None:
    # Explicit CANCELLED edge: allowed from any STAGE_CANCELLABLE source.
    if target is _FS.CANCELLED:
        if current in STAGE_CANCELLABLE:
            return
        raise StageTransitionError(current, target)
    # Otherwise use the normal forward graph.
    if target not in STAGE_TRANSITIONS.get(current, frozenset()):
        raise StageTransitionError(current, target)


def stage_is_terminal(stage: OrderFulfillmentStage) -> bool:
    return stage in STAGE_TERMINAL


# ---------------------------------------------------------------------------
# Per-stage proof / evidence requirements.
#
# 6 transitions require the operator to attach proof artifacts in the
# ``meta`` JSONB payload before StageService.set_stage will accept the
# transition. force=True bypasses the check but writes a ``[FORCE]``
# marker in the history reason so the bypass is auditable.
#
# Required keys map per stage (at least one alternative satisfies the
# rule — keys grouped with ``|`` are OR; otherwise AND).
#
#   READY_TO_SHIP        seller_user_id, packed_at
#   PICKED_UP            rider_id, pickup_scan_id
#   AT_HUB               hub_id, hub_scan_id
#   OUT_FOR_DELIVERY     rider_id, parcel_scan_id
#   DELIVERED            pod_photo_url | signature_url
#                        + cod_collected_minor (if payment_method=cod, enforced at endpoint)
#   RETURNED_TO_SELLER   handover_photo_url, seller_signature_url | seller_otp
# ---------------------------------------------------------------------------


# A "spec" is a list of groups; each group is a tuple of alternative keys.
# At least one key in every group must be present.
STAGE_REQUIRED_META: dict[OrderFulfillmentStage, list[tuple[str, ...]]] = {
    _FS.READY_TO_SHIP: [("seller_user_id",), ("packed_at",)],
    _FS.PICKED_UP: [("rider_id",), ("pickup_scan_id",)],
    _FS.AT_HUB: [("hub_id",), ("hub_scan_id",)],
    _FS.OUT_FOR_DELIVERY: [("rider_id",), ("parcel_scan_id",)],
    _FS.DELIVERED: [("pod_photo_url", "signature_url")],
    _FS.RETURNED_TO_SELLER: [
        ("handover_photo_url",),
        ("seller_signature_url", "seller_otp"),
    ],
}


class StageProofError(Exception):
    """Raised when the transition target's required proof keys are
    missing from the supplied meta payload."""

    def __init__(
        self,
        target: OrderFulfillmentStage,
        missing_groups: list[tuple[str, ...]],
    ) -> None:
        msg_parts = [" | ".join(g) for g in missing_groups]
        super().__init__(
            f"Stage {target.value!r} requires proof: "
            + " AND ".join(f"({m})" for m in msg_parts),
        )
        self.target = target
        self.missing_groups = missing_groups


def assert_proof_complete(
    target: OrderFulfillmentStage,
    meta: dict | None,
) -> None:
    """Raise :class:`StageProofError` if ``meta`` is missing any required
    proof keys for ``target``. No-op when the stage has no requirements.
    """
    spec = STAGE_REQUIRED_META.get(target)
    if not spec:
        return
    payload = meta or {}
    missing: list[tuple[str, ...]] = []
    for group in spec:
        if not any(payload.get(k) not in (None, "", []) for k in group):
            missing.append(group)
    if missing:
        raise StageProofError(target, missing)


# Mapping used by the 0080 backfill + by services that need to derive a stage
# when only the legacy 9-state status is known.
STATUS_TO_STAGE: dict[OrderStatus, OrderFulfillmentStage] = {
    OrderStatus.PENDING_PAYMENT: _FS.PAYMENT_PENDING,
    OrderStatus.PAYMENT_CONFIRMED: _FS.PAYMENT_VERIFIED,
    OrderStatus.STOCK_RESERVED: _FS.SELLER_PROCESSING,
    OrderStatus.APPROVED: _FS.SELLER_PROCESSING,
    OrderStatus.PACKING: _FS.READY_TO_SHIP,
    OrderStatus.OUT_FOR_DELIVERY: _FS.OUT_FOR_DELIVERY,
    OrderStatus.COMPLETED: _FS.DELIVERED,
    OrderStatus.CANCELLED: _FS.CANCELLED,
    OrderStatus.FAILED: _FS.CANCELLED,
}
