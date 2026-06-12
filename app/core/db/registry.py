"""Central import point for every ORM model.

Alembic's autogenerate only sees tables that have been imported into the
metadata before it inspects ``Base.metadata``. Each domain module's
``models`` submodule must be referenced here so migrations stay complete.
"""

from __future__ import annotations


def import_all_models() -> None:
    # Core tables (audit log, outbox, idempotency keys).
    import app.core.audit.models
    import app.core.events.models
    import app.core.idempotency_models
    import app.modules.catalog.models

    # Domain modules — added as phases land.
    import app.modules.iam.models  # noqa: F401  (Phase 1)
    import app.modules.inventory.models  # noqa: F401  (Phase 3)
    import app.modules.inventory.operations_models  # noqa: F401  (Phase C 2026-05-26)
    import app.modules.supervisor_lm.models  # noqa: F401  (Phase D 2026-05-26)
    import app.modules.mother_qr.models  # noqa: F401  (Phase E 2026-05-26)
    import app.modules.rider_kyc.models  # noqa: F401  (rider mobile compat — 2026-05-29)
    import app.modules.orders.models  # noqa: F401  (Phase 4)
    import app.modules.delivery.models  # noqa: F401  (Phase 4)
    import app.modules.packing.models  # noqa: F401  (Phase 4)
    import app.modules.deliveries.models  # noqa: F401  (Phase 4)
    import app.modules.returns.models  # noqa: F401  (Phase 4)
    import app.modules.finance.models  # noqa: F401  (Phase 5)
    import app.modules.finance.operations_models  # noqa: F401  (Phase B 2026-05-26)
    import app.modules.ai.models  # noqa: F401  (Phase 6)
    import app.modules.mobile.models  # noqa: F401  (Phase 6)
    import app.modules.mobile_auth.models  # noqa: F401  (mobile PIN/biometric — 2026-06-01)
    import app.modules.payments.models  # noqa: F401  (Module 22)
    import app.modules.whatsapp_webhook.models  # noqa: F401  (Module 26)
    import app.modules.search.models  # noqa: F401  (Module 28)
    import app.modules.product_videos.models  # noqa: F401  (Module 35 — product page videos)
    import app.modules.cart.models  # noqa: F401  (Phase B-1 — cart + line items)
    import app.modules.checkout.models  # noqa: F401  (Phase B-1 — checkout sessions)
    # Phase B-2 — customer-facing engagement modules.
    import app.modules.loyalty.models  # noqa: F401  (loyalty accounts + ledger)
    import app.modules.affiliates.models  # noqa: F401  (share-and-earn records)
    # Phase B-3 (Daraz/Noon parity).
    import app.modules.wishlist.models  # noqa: F401  (save-for-later)
    import app.modules.coupons.models  # noqa: F401  (coupon codes + redemptions)
    import app.modules.notifications.models  # noqa: F401  (in-app notification feed)
    # Phase B-3 expanded — added 2026-05-12 expanded build.
    import app.modules.analytics.models  # noqa: F401  (analytics_events)
    import app.modules.feature_flags.models  # noqa: F401  (feature_flags)
    import app.modules.fraud.models  # noqa: F401  (fraud_assessments)
    import app.modules.gift_cards.models  # noqa: F401  (gift_cards + redemptions)
    import app.modules.referrals.models  # noqa: F401  (referral codes + attribution)
    import app.modules.tax_rules.models  # noqa: F401  (tax_rules)
    import app.modules.support_tickets.models  # noqa: F401  (router landed 2026-05-16)
    import app.modules.wallet.models  # noqa: F401  (e-commerce wallet, 2026-05-16)
    # Funnel — added 2026-05-13 (merged_funnel_kpi).
    import app.modules.funnel.models  # noqa: F401  (customer behavior + KPI tracking)
    # Sponsored Products advertising platform — Phase 1.A skeleton (2026-05-17).
    import app.modules.ads.models  # noqa: F401  (7 hypershop_ad_* tables, mig 0067)
    # Couriers — external courier integrations (Pathao, RedX, Sundarban,
    # Steadfast). Phase M2.A skeleton (2026-05-17) — migration 0069.
    import app.modules.couriers.models  # noqa: F401  (5 hypershop_courier_* tables)
    # Cart recovery — abandoned cart + win-back tiered reminders. Phase
    # M3.A (2026-05-17) — migration 0070.
    import app.modules.cart_recovery.models  # noqa: F401  (2 hypershop_cart_recovery_* tables)
    # Disputes — buyer/seller/mediator dispute resolution + escrow holds.
    # Phase M4 (2026-05-17) — migration 0072.
    import app.modules.disputes.models  # noqa: F401  (4 hypershop_dispute*/escrow_holds tables)
    # CC unified inbox + voice calls + CSAT (2026-05-18) — migration 0073.
    # Four new tables; separate from Module 47 legacy CC models.
    import app.modules.customer_care.cc_inbox_models  # noqa: F401
    # Seller rating (Phase M5 — 2026-05-18) — migration 0074. Per-seller
    # quality score + history snapshots feeding search ranking, payout
    # priority, and storefront badge.
    import app.modules.seller_rating.models  # noqa: F401
    # M6 customer segmentation — RFM cohorts + named audiences (2026-05-18).
    # Migration 0075. Three new tables (rfm scores + segments + memberships).
    import app.modules.customer_segments.models  # noqa: F401
    # M7 bulk product upload — seller CSV/XLSX ingest (2026-05-18) — migration 0076.
    import app.modules.bulk_upload.models  # noqa: F401
