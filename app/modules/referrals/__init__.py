"""Referrals — phase-1 invite-code program (built 2026-05-11).

Locked spec:
  * Code: 8-char base32 (no I, O, 1, 0 — unambiguous). Generated lazily
    on first ``GET /referrals/mine`` call.
  * Reward: referrer gets 100 loyalty points + referee gets 50 points
    when the referee's first order reaches ``payment_confirmed``.
  * Attribution is one-shot per referee — once a referee_user_id is
    written, no future signup can overwrite it.
  * Self-referral is rejected at attribution time.
"""
