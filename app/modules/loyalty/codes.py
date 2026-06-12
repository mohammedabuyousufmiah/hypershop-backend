"""Loyalty policy constants — locked 2026-05-11."""
from __future__ import annotations

from decimal import Decimal

# Earn — 1 point per 100 BDT spent (subtotal floor-div 100).
POINTS_PER_BDT_DIVISOR = 100

# Tier ladder — lifetime points thresholds.
TIER_BRONZE_MIN = 100
TIER_SILVER_MIN = 200
TIER_GOLD_MIN = 300

ALL_TIERS = ("NONE", "BRONZE", "SILVER", "GOLD")

# Redemption ratio: 1 point = 0.50 BDT.
REDEEM_BDT_PER_POINT = Decimal("0.50")

# Max redemption cap per order = 3% of subtotal.
MAX_REDEEM_PCT_OF_SUBTOTAL = Decimal("0.03")

# Earn rows expire 12 months after creation.
EARN_EXPIRY_DAYS = 365


def compute_tier(lifetime_earned_points: int) -> str:
    if lifetime_earned_points >= TIER_GOLD_MIN:
        return "GOLD"
    if lifetime_earned_points >= TIER_SILVER_MIN:
        return "SILVER"
    if lifetime_earned_points >= TIER_BRONZE_MIN:
        return "BRONZE"
    return "NONE"


def compute_earn_points(subtotal_minor: int) -> int:
    """``subtotal_minor`` is amount in paisa (BDT * 100). Earn 1 pt per
    100 BDT spent — i.e. ``subtotal_bdt // 100``."""
    subtotal_bdt = subtotal_minor // 100
    return subtotal_bdt // POINTS_PER_BDT_DIVISOR


def compute_max_redeem_points(subtotal_minor: int) -> int:
    """Cap = 3% of subtotal in BDT, converted back to points.

    ``max_bdt = subtotal_bdt * 0.03``;
    ``max_points = floor(max_bdt / 0.50) = floor(subtotal_bdt * 0.06)``.
    """
    subtotal_bdt = Decimal(subtotal_minor) / Decimal(100)
    max_bdt = subtotal_bdt * MAX_REDEEM_PCT_OF_SUBTOTAL
    return int(max_bdt / REDEEM_BDT_PER_POINT)


def points_to_minor(points: int) -> int:
    """``points`` -> discount in paisa (BDT * 100). 1pt = 0.50 BDT = 50p."""
    return int(Decimal(points) * REDEEM_BDT_PER_POINT * Decimal(100))
