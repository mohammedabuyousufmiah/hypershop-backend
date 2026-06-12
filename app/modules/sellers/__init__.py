"""Multi-seller / marketplace module — phase 1 of 6.

Phase 1 ships the data foundation (sellers + seller_users tables)
and admin-facing CRUD lifecycle: create → submit_kyc → approve /
reject / suspend. Phases 2–6 (product ownership, seller dashboard,
self-serve onboarding, payouts, frontend portal) are not in scope
here — see ``docs/SCOPING_MULTISELLER.md``.

Phase-1 design decisions (all reversible without schema changes —
controlled via service-layer constants and admin-set per-seller
fields):

  * Single-seller per product (Daraz-style). Multi-offer (Amazon-
    style) would need a separate ``seller_offers`` table; that's a
    later phase decision.
  * First-party + seller-as-exception. The migration script seeds
    a "Hypershop Direct" seller representing first-party catalog;
    every existing product can later be backfilled to that seller_id
    when phase 2 lands.
  * KYC fields are columns on the seller row, not a separate table.
    All four BD-typical fields exist (TIN, NID, bank, trade
    license) but are nullable so legal can decide which are
    mandatory at audit time.
  * Commission is a per-seller percentage column (default 10.00) —
    tunable per-row by admin until the CFO settles on a tier model
    in phase 5.
  * Payout cadence is a per-seller enum (weekly / biweekly /
    monthly, default monthly) — adjustable per row.
  * Onboarding is admin-invite-only in v1 (no public registration
    endpoint); self-serve lands in phase 4.
  * Returns liability is NOT modelled here — it's a settlement-time
    decision in phase 5's payout engine, not a seller-row field.
"""
