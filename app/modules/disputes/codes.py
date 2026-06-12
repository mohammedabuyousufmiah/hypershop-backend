"""Constants for disputes module — types, statuses, resolutions, roles."""
from __future__ import annotations

# ─── Dispute types ────────────────────────────────────────────────
DISPUTE_TYPE_WRONG_ITEM = "wrong_item"
DISPUTE_TYPE_DAMAGED = "damaged"
DISPUTE_TYPE_NOT_RECEIVED = "not_received"
DISPUTE_TYPE_QUALITY = "quality_issue"
DISPUTE_TYPE_FAKE = "fake_item"
DISPUTE_TYPE_BILLING = "billing_error"
DISPUTE_TYPE_OTHER = "other"
ALL_DISPUTE_TYPES = (
    DISPUTE_TYPE_WRONG_ITEM,
    DISPUTE_TYPE_DAMAGED,
    DISPUTE_TYPE_NOT_RECEIVED,
    DISPUTE_TYPE_QUALITY,
    DISPUTE_TYPE_FAKE,
    DISPUTE_TYPE_BILLING,
    DISPUTE_TYPE_OTHER,
)

# ─── Dispute statuses ─────────────────────────────────────────────
STATUS_OPEN = "open"
STATUS_AWAITING_SELLER = "awaiting_seller"
STATUS_AWAITING_BUYER = "awaiting_buyer"
STATUS_UNDER_REVIEW = "under_review"
STATUS_RESOLVED = "resolved"
STATUS_CLOSED = "closed"
ALL_STATUSES = (
    STATUS_OPEN,
    STATUS_AWAITING_SELLER,
    STATUS_AWAITING_BUYER,
    STATUS_UNDER_REVIEW,
    STATUS_RESOLVED,
    STATUS_CLOSED,
)
LIVE_STATUSES = (
    STATUS_OPEN,
    STATUS_AWAITING_SELLER,
    STATUS_AWAITING_BUYER,
    STATUS_UNDER_REVIEW,
)

# ─── Resolutions ──────────────────────────────────────────────────
RESOLUTION_REFUND_FULL = "refund_full"
RESOLUTION_REFUND_PARTIAL = "refund_partial"
RESOLUTION_REPLACE = "replace"
RESOLUTION_DECLINE = "decline"
RESOLUTION_CUSTOMER_WITHDREW = "customer_withdrew"
ALL_RESOLUTIONS = (
    RESOLUTION_REFUND_FULL,
    RESOLUTION_REFUND_PARTIAL,
    RESOLUTION_REPLACE,
    RESOLUTION_DECLINE,
    RESOLUTION_CUSTOMER_WITHDREW,
)

# ─── Roles ────────────────────────────────────────────────────────
ROLE_BUYER = "buyer"
ROLE_SELLER = "seller"
ROLE_MEDIATOR = "mediator"
ROLE_SYSTEM = "system"

# ─── Escrow hold statuses ─────────────────────────────────────────
ESCROW_STATUS_ACTIVE = "active"
ESCROW_STATUS_RELEASED_TO_BUYER = "released_to_buyer"
ESCROW_STATUS_RELEASED_TO_SELLER = "released_to_seller"
ESCROW_STATUS_SPLIT = "split"
ESCROW_STATUS_CANCELLED = "cancelled"
ALL_ESCROW_STATUSES = (
    ESCROW_STATUS_ACTIVE,
    ESCROW_STATUS_RELEASED_TO_BUYER,
    ESCROW_STATUS_RELEASED_TO_SELLER,
    ESCROW_STATUS_SPLIT,
    ESCROW_STATUS_CANCELLED,
)

# ─── Policy knobs ─────────────────────────────────────────────────
SELLER_RESPONSE_SLA_HOURS = 48
MAX_EVIDENCE_BYTES = 5 * 1024 * 1024
MAX_EVIDENCE_FILES = 10
