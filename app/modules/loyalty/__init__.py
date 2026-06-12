"""Loyalty — phase-1 customer points program (built 2026-05-11).

Locked spec:
  * Earn: 1 point per 100 BDT spent (truncating).
  * Tiers (ROLLING 12-month earn — switched from lifetime 2026-05-11):
    BRONZE >= 100 pts earned in last 365 days,
    SILVER >= 200, GOLD >= 300. Tier may drop when old EARN rows age
    out of the window — matches the expiry policy on the balance side.
  * Redemption: 1 point = 0.50 BDT; capped at 3% of order subtotal.
  * Expiry: 12 months rolling, recorded per-EARN-row at ``expires_at``.

Schema:
  * ``loyalty_accounts`` — one row per user; ``balance_points`` is
    denormalised but kept consistent by the service. ``tier`` is also
    denormalised; ``compute_tier(lifetime_earned_points)`` is the
    source of truth.
  * ``loyalty_transactions`` — append-only ledger. ``points`` is signed
    (+EARN / -REDEEM / -EXPIRE). The service writes ledger rows and the
    account row together inside one transaction; balance drift is the
    sign that integration broke.

Integration points:
  * ``checkout.apply_loyalty`` (existing stub) is replaced — now it
    actually calls ``LoyaltyService.preview_redeem`` to compute the
    points-applied + discount-applied, then ``redeem`` inside the
    confirm step.
  * Order PAID — order service emits a points-earn call into
    ``LoyaltyService.earn`` with the order subtotal.

Out of scope for phase 1:
  * Tier benefits (the ``tier`` field is read by deal-engine consumers
    elsewhere; we don't gate any feature on it here).
  * Admin balance-adjust UI (the ADJUST txn_type is wired so service
    callers can issue compensating points; UI is later).
"""
