"""Affiliates policy constants — locked 2026-05-11."""
from __future__ import annotations

from decimal import Decimal

# Default commission as a fraction of subtotal_minor.
DEFAULT_COMMISSION_RATE = Decimal("0.05")

# Code generator — same alphabet as referrals (unambiguous base32).
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8

# 1 BDT = 2 loyalty points (matches the loyalty module's redeem ratio).
LOYALTY_POINTS_PER_BDT = 2
