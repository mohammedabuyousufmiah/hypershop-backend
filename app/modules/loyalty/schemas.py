"""Pydantic v2 wire schemas for loyalty."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class LoyaltyAccountOut(BaseModel):
    """Snapshot of the caller's loyalty state — the storefront's
    account-overview + checkout widget consume this."""

    model_config = ConfigDict(from_attributes=True)

    balance_points: int
    # Tier qualifies on a ROLLING 12-month window — this is the sum of
    # EARN points in the last 365 days. ``lifetime_earned_points`` is
    # retained for audit but does NOT drive tier.
    rolling_earned_points: int
    lifetime_earned_points: int
    tier: str
    # Forward-progress hint — how many more points need to be earned
    # within the rolling window to reach the next tier. Null at GOLD.
    points_to_next_tier: int | None = None
    next_tier: str | None = None


class LoyaltyTransactionOut(BaseModel):
    """One ledger row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    txn_type: str
    points: int
    source_order_id: UUID | None
    expires_at: datetime | None
    reason: str | None
    created_at: datetime


class LoyaltyTransactionListOut(BaseModel):
    items: list[LoyaltyTransactionOut]
    total: int


class LoyaltyAvailabilityOut(BaseModel):
    """Redemption preview — what the checkout widget asks for after the
    cart is priced but before the user commits to a redemption amount.

    ``max_points`` is min(balance, subtotal_cap_points).
    ``max_discount_minor`` is the BDT-paisa value of ``max_points``.
    """

    balance_points: int
    subtotal_minor: int
    max_points: int
    max_discount_minor: int
