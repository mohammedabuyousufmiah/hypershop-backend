"""Audit action codes for the rider wallet module."""

from __future__ import annotations

# ----- Wallet lifecycle -----
ACTION_WALLET_CREATED = "rider_wallet.created"
ACTION_WALLET_LOCKED = "rider_wallet.locked"
ACTION_WALLET_UNLOCKED = "rider_wallet.unlocked"
ACTION_WALLET_FROZEN = "rider_wallet.frozen"
ACTION_WALLET_UNFROZEN = "rider_wallet.unfrozen"

# ----- Ledger -----
ACTION_COD_COLLECTED = "rider_wallet.cod_collected"
ACTION_LEDGER_ADJUSTED = "rider_wallet.ledger_adjusted"

# ----- Settlement -----
ACTION_SETTLEMENT_SUBMITTED = "rider_wallet.settlement_submitted"
ACTION_SETTLEMENT_VERIFIED = "rider_wallet.settlement_verified"
ACTION_SETTLEMENT_REJECTED = "rider_wallet.settlement_rejected"
ACTION_SETTLEMENT_ADJUSTED = "rider_wallet.settlement_adjusted"

# ----- Carry-forward -----
ACTION_CARRY_FORWARD_APPROVED = "rider_wallet.carry_forward_approved"
ACTION_CARRY_FORWARD_REJECTED = "rider_wallet.carry_forward_rejected"
ACTION_CARRY_FORWARD_EXPIRED = "rider_wallet.carry_forward_expired"

# ----- Daily summary -----
ACTION_DAILY_SUMMARY_CLOSED = "rider_wallet.daily_summary_closed"

# ----- Cash limits -----
ACTION_CASH_LIMIT_UPDATED = "rider_wallet.cash_limit_updated"
