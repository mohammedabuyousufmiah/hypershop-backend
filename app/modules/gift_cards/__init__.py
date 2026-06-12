"""Gift cards — pre-funded codes that credit a customer's wallet on redeem.

One table:
  * ``gift_cards`` — code-unique, face-value-in-minor-units, status in
    {active, redeemed, expired, revoked}. Purchase flow is out of
    scope for v1 (cards are seeded via admin); redeem flow credits
    the redeemer's loyalty balance with the face value as a one-shot
    ADJUST transaction.

Why not just stuff this in loyalty: loyalty is points-denominated and
has its own earn/burn semantics. Gift cards are money-denominated and
need their own audit trail (purchaser ≠ redeemer, expiry, revocation).
We bridge to loyalty on redeem so existing balance-display surfaces
"just work" without a second wallet UI.
"""
