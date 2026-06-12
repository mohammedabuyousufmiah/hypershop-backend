"""Module-level constants for the couriers module.

Mirror the DB check constraints in alembic 0069.
"""
from __future__ import annotations

# Provider codes — match the seeded rows in migration 0069.
PROVIDER_PATHAO = "pathao"
PROVIDER_REDX = "redx"
PROVIDER_SUNDARBAN = "sundarban"
PROVIDER_STEADFAST = "steadfast"
# Local in-house couriers — bridges to the Deliveries module's rider roster
# instead of an external 3PL API. No external credentials required.
PROVIDER_LOCAL = "local"
ALL_PROVIDERS = (
    PROVIDER_PATHAO,
    PROVIDER_REDX,
    PROVIDER_SUNDARBAN,
    PROVIDER_STEADFAST,
    PROVIDER_LOCAL,
)

# Shipment status (mirror DB check constraint).
STATUS_CREATED = "created"
STATUS_PICKUP_PENDING = "pickup_pending"
STATUS_IN_TRANSIT = "in_transit"
STATUS_OUT_FOR_DELIVERY = "out_for_delivery"
STATUS_DELIVERED = "delivered"
STATUS_RETURNED = "returned"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"
STATUS_EXCEPTION = "exception"

ALL_STATUSES = (
    STATUS_CREATED,
    STATUS_PICKUP_PENDING,
    STATUS_IN_TRANSIT,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_DELIVERED,
    STATUS_RETURNED,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_EXCEPTION,
)

# Live statuses — a shipment in any of these blocks creating a new one
# for the same order.
LIVE_STATUSES = frozenset({
    STATUS_CREATED,
    STATUS_PICKUP_PENDING,
    STATUS_IN_TRANSIT,
    STATUS_OUT_FOR_DELIVERY,
})

TERMINAL_STATUSES = frozenset({
    STATUS_DELIVERED,
    STATUS_RETURNED,
    STATUS_CANCELLED,
})

# Service type.
SERVICE_REGULAR = "regular"
SERVICE_EXPRESS = "express"
SERVICE_SAME_DAY = "same_day"
SERVICE_NEXT_DAY = "next_day"

ALL_SERVICE_TYPES = (
    SERVICE_REGULAR,
    SERVICE_EXPRESS,
    SERVICE_SAME_DAY,
    SERVICE_NEXT_DAY,
)

# COD remittance status.
REMIT_PENDING = "pending"
REMIT_SETTLED = "settled"
REMIT_DISPUTED = "disputed"
REMIT_RECONCILED = "reconciled"

ALL_REMIT_STATUSES = (
    REMIT_PENDING,
    REMIT_SETTLED,
    REMIT_DISPUTED,
    REMIT_RECONCILED,
)

# Environment values for credential rows.
ENV_SANDBOX = "sandbox"
ENV_PRODUCTION = "production"
