"""Outbox event types emitted by the rider wallet module.

Used to decouple notification sends (SMS to rider, email to ops) from
the transactional verify/reject path. Send failures retry via the
outbox dispatcher's exponential backoff rather than rolling back the
finance decision.
"""

from __future__ import annotations

EVT_SETTLEMENT_VERIFIED = "rider_wallet.settlement.verified"
EVT_SETTLEMENT_REJECTED = "rider_wallet.settlement.rejected"
EVT_SETTLEMENT_ADJUSTED = "rider_wallet.settlement.adjusted"
EVT_WALLET_LOCKED = "rider_wallet.wallet.locked"
EVT_WALLET_FROZEN = "rider_wallet.wallet.frozen"
