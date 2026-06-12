"""Referrals policy constants — locked 2026-05-11."""
from __future__ import annotations

# Reward points (paid out via the loyalty module's ADJUST txn).
REFERRER_REWARD_POINTS = 100
REFEREE_REWARD_POINTS = 50

# Code generator: 8-character alphabet excluding ambiguous glyphs.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8

# Attribution status values.
STATUS_PENDING = "PENDING"
STATUS_REWARDED = "REWARDED"
STATUS_EXPIRED = "EXPIRED"
ALL_STATUSES = (STATUS_PENDING, STATUS_REWARDED, STATUS_EXPIRED)
