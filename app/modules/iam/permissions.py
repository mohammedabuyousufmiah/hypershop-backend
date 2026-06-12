"""Canonical permission and role catalog for the IAM module.

Permissions follow ``<module>.<resource>.<action>[.<scope>]`` so we can grep
for a permission name and find every site that grants or requires it.
Roles are seeded from this file via the bootstrap CLI command, so this is
the single source of truth — never insert role/permission rows manually.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------- IAM permissions ----------
P_USER_READ_SELF = "iam.user.read.self"
P_USER_UPDATE_SELF = "iam.user.update.self"
P_USER_READ_ANY = "iam.user.read.any"
P_USER_UPDATE_ANY = "iam.user.update.any"
P_USER_DELETE_ANY = "iam.user.delete.any"
P_USER_CREATE = "iam.user.create"  # create internal user + assign role (POST /admin/iam/users)
P_USER_LIST = "iam.user.list"
P_ROLE_READ = "iam.role.read"
P_ROLE_ASSIGN = "iam.role.assign"
P_AUDIT_READ = "iam.audit.read"
P_SESSION_REVOKE_ANY = "iam.session.revoke.any"

# Catalog (Phase 2) — declared here for forward reference; seeded once Phase 2 lands.
P_CATALOG_PRODUCT_READ = "catalog.product.read"
P_CATALOG_PRODUCT_WRITE = "catalog.product.write"
P_CATALOG_CATEGORY_WRITE = "catalog.category.write"

# Inventory (Phase 3)
P_INVENTORY_READ = "inventory.read"
P_INVENTORY_RECEIVE = "inventory.receive"
P_INVENTORY_ADJUST = "inventory.adjust"
P_INVENTORY_COUNT_APPROVE = "inventory.count.approve"

# Cart + Orders (Phase 4)
P_CART_USE = "cart.use"
P_ORDER_PLACE = "order.place"
P_ORDER_READ_SELF = "order.read.self"
P_ORDER_READ_ANY = "order.read.any"
P_ORDER_FULFILL = "order.fulfill"
P_ORDER_CANCEL_ANY = "order.cancel.any"

# Payments (Phase 5)
P_PAYMENT_REFUND = "payment.refund"
P_PAYMENT_READ_ANY = "payment.read.any"

# Finance (Phase 5 — accounting + reporting)
P_FINANCE_READ = "finance.read"
P_FINANCE_POST = "finance.post"  # post journal entries, book bills, accrue refunds
P_FINANCE_SETTLE = "finance.settle"  # pay supplier bills, pay refunds, deposit COD
P_FINANCE_CLOSE = "finance.close"  # daily/period close
P_FINANCE_ADJUST = "finance.adjust"  # reverse posted entries, post adjustments

# Dashboard (read-only aggregations across modules)
P_DASHBOARD_READ = "dashboard.read"

# AI services (assistive only — never auto-decisions)
P_AI_USE = "ai.use"  # trigger an AI capability (OCR, suggest, predict, fraud)
P_AI_READ = "ai.read"  # read past proposals + audit

# Reporting platform (Module 30)
P_REPORTING_ADMIN = "reporting.admin"   # /admin/reporting/* — config + audit feed

# Rider routing + wallet (Modules 31-32)
P_RIDER_APP = "rider.app"           # rider mobile-app endpoints (self-serve)
P_RIDER_DISPATCH = "rider.dispatch"  # ops dispatch console + finance wallet review

# SEO + dynamic content (Module 34) — admin write reuses catalog.write

# Reviews (phase-1) — verified-purchase 1-5 star reviews, admin moderation
P_REVIEWS_WRITE = "reviews.write"          # customer creates/edits own review + helpful vote
P_REVIEWS_ADMIN = "reviews.admin"          # admin moderation queue + approve/reject/disable

# Sellers / marketplace (phase-1) — admin onboarding only; phase 4 adds self-serve
P_SELLERS_READ = "sellers.read"            # read own seller row (phase 3 — placeholder for now)
P_SELLERS_WRITE = "sellers.write"          # write to own seller row (phase 3 — placeholder)
P_SELLERS_ADMIN = "sellers.admin"          # admin onboarding + KYC moderation + commission config

# Funnel — customer behavior tracking + retargeting + KPI dashboard
# (added 2026-05-13). Split into three because /retargeting/export
# ships customer PII out to ad platforms (Meta/Google/TikTok) and
# /events/track is high-volume write traffic — neither should ride on
# the same gate as read-only KPI viewing. See app/modules/funnel/
# security.py for the FastAPI deps that consume these strings.
P_FUNNEL_VIEW = "funnel.view"              # read /api/v1/funnel/kpi/* + /customers + /followup-tasks
P_FUNNEL_TRACK = "funnel.track"            # write /api/v1/funnel/events/track
P_FUNNEL_EXPORT = "funnel.export"          # /api/v1/funnel/retargeting/export — ships PII to ad networks

# Growth modules — admin-managed loyalty, referrals, gift cards, affiliates,
# coupons. These perms are referenced by app/core/registry/admin_modules.py
# for the Growth sidebar group. Granted to admin + marketing_manager
# (loyalty + affiliates + coupons are growth tools); finance_manager gets
# gift_cards.admin because gift-card liability sits on the balance sheet.
#
# coupons.admin was introduced 2026-05-16 to replace the previous
# ``catalog.product.read`` gate on the Coupons admin module — that gate
# was too loose (every browsing customer satisfied it). Now scoped to
# the four roles that actually run promotions.
P_AFFILIATES_ADMIN = "affiliates.admin"
P_LOYALTY_ADMIN = "loyalty.admin"
P_REFERRALS_ADMIN = "referrals.admin"
P_GIFT_CARDS_ADMIN = "gift_cards.admin"
P_COUPONS_ADMIN = "coupons.admin"

# Sponsored Products advertising (Phase 1.A — 2026-05-17). Two-tier:
# ads.view satisfies read-only admin oversight (revenue rollup, invalid-
# click audit); ads.manage is needed to create/edit campaigns + ad groups,
# top up the ad wallet, suspend/refund. Sellers get ads.manage on their
# own row; marketing/seller managers get it platform-wide.
P_ADS_VIEW = "ads.view"
P_ADS_MANAGE = "ads.manage"

# Couriers — external courier integrations (Pathao, RedX, Sundarban,
# Steadfast). Phase M2.A — 2026-05-17. Two-tier: couriers.view satisfies
# read-only oversight of providers + shipments; couriers.manage gates
# credential CRUD, provider enable/disable, refresh-status, cancel.
P_COURIERS_VIEW = "couriers.view"
P_COURIERS_MANAGE = "couriers.manage"

# Cart recovery — abandoned-cart reminders + win-back automation
# (Phase M3.B + M3.C — 2026-05-17). Two-tier: cart_recovery.view for
# KPI summary + dispatch / suppression read; cart_recovery.manage gates
# manual suppression add/remove. Public opt-out has no perm gate.
P_CART_RECOVERY_VIEW = "cart_recovery.view"
P_CART_RECOVERY_MANAGE = "cart_recovery.manage"

# Disputes — buyer/seller/mediator dispute resolution + escrow holds
# (Phase M4 — 2026-05-17). Three-tier: disputes.view satisfies read-only
# admin oversight (list/escrow audit); disputes.manage gates mediator
# assignment + close; disputes.mediate is the actual final-decision verb
# (refund_full/partial/replace/decline) that releases escrow.
P_DISPUTES_VIEW = "disputes.view"
P_DISPUTES_MANAGE = "disputes.manage"
P_DISPUTES_MEDIATE = "disputes.mediate"

# Seller rating — per-seller quality score + tier feeding search ranking,
# payout priority, and storefront badge (Phase M5 — 2026-05-18). Two-tier:
# .view satisfies read-only KPI + history; .manage gates admin override
# (suspend/unsuspend) + on-demand recompute. Granted to admin + manager +
# seller_manager.
P_SELLER_RATINGS_VIEW = "seller_ratings.view"
P_SELLER_RATINGS_MANAGE = "seller_ratings.manage"

# M7 bulk product upload (2026-05-18). Two-tier: bulk_upload.view for
# admin oversight (list jobs across sellers); bulk_upload.manage for
# admin force-retry. Seller endpoints are gated by get_current_seller_id.
P_BULK_UPLOAD_VIEW = "bulk_upload.view"
P_BULK_UPLOAD_MANAGE = "bulk_upload.manage"

# Customer segmentation engine (M6 — 2026-05-18). Two-tier: .view satisfies
# read-only admin oversight (list segments + RFM lookup + CSV export);
# .manage gates segment CRUD + on-demand recompute. Granted to admin +
# manager + marketing_manager.
P_CUSTOMER_SEGMENTS_VIEW = "customer_segments.view"
P_CUSTOMER_SEGMENTS_MANAGE = "customer_segments.manage"


# Customer Care (Module 47 — AI WhatsApp customer care + sales automation,
# added 2026-05-13). All routes mounted under /api/v1/customer-care/* and
# the agent PWA at /customercare. Three-tier permission split so a normal
# agent can't, e.g., reindex the RAG corpus or trigger a Google-Sheets sync.
P_CC_AGENT = "customercare.agent"          # inbox read/reply, conversation ops, send WhatsApp
P_CC_ADMIN = "customercare.admin"          # agent management, SLA config, follow-up campaigns
P_CC_RAG_ADMIN = "customercare.rag.admin"  # knowledge-base ingest/delete/reindex


# ─── Plural / short-form permission aliases (added 2026-05-16) ──────
# A second naming convention introduced when ops teams asked for
# shorter, action-oriented perm names that match how they verbalise
# the work in standups ("orders view", "payments reconcile") rather
# than the formal ``<module>.<resource>.<action>`` form.
#
# Some are TRUE ALIASES of existing perms — both strings exist in the
# catalog and a role can hold either to satisfy an endpoint check.
# Endpoint code should declare requirements using whichever name is
# clearer; both names sit in the catalog so the alias stays grep-able
# from either direction.
#
# Others are NEW capabilities with no prior equivalent
# (orders.update, payments.reconcile, riders.cod_settle, sellers.approve,
# ai_care.view, voice_call.assign, seo.configure). Those gate brand-new
# endpoints once they land.

# Orders
P_ORDERS_VIEW = "orders.view"          # alias for P_ORDER_READ_ANY
P_ORDERS_UPDATE = "orders.update"      # NEW — edit a placed order (address, line qty, notes)
P_ORDERS_CANCEL = "orders.cancel"      # alias for P_ORDER_CANCEL_ANY

# Payments
P_PAYMENTS_VIEW = "payments.view"          # alias for P_PAYMENT_READ_ANY
P_PAYMENTS_RECONCILE = "payments.reconcile"  # NEW — match gateway settlement against orders

# Riders
P_RIDERS_COD_SETTLE = "riders.cod_settle"  # NEW — record COD handover from rider to finance

# Sellers
P_SELLERS_APPROVE = "sellers.approve"      # NEW — granular: approve/reject KYC + onboarding

# AI care (Module 47 inbox + voice insights)
P_AI_CARE_VIEW = "ai_care.view"            # NEW — read AI-flagged conversation triage + summaries
P_VOICE_CALL_ASSIGN = "voice_call.assign"  # NEW — route inbound calls to a specific agent
P_VOICE_CALL_VIEW = "voice_call.view"      # NEW (2026-05-18) — read voice call sessions + transcripts

# CC unified inbox (2026-05-18, migration 0073) — distinct from the
# legacy customercare.* triad (which gates the Module 47 WhatsApp
# specialist PWA). These three gate the admin-shell unified inbox
# (WhatsApp + email + SMS) + CSAT console.
P_CC_INBOX_VIEW = "cc_inbox.view"          # NEW — list/read threads, messages, CSAT
P_CC_INBOX_REPLY = "cc_inbox.reply"        # NEW — send a reply, resolve a thread, AI-draft
P_CC_INBOX_ADMIN = "cc_inbox.admin"        # NEW — assign agents, close/spam threads

# SEO
P_SEO_CONFIGURE = "seo.configure"      # NEW — admin-managed SEO bundles, redirects, sitemap
P_SEO_VIEW = "seo.view"                # NEW — read-only audit + reports
P_SEO_BULK_PUBLISH = "seo.bulk_publish"  # NEW 2026-05-28 — programmatic/entity/backlink seeders
P_SEO_CRON_RUN = "seo.cron_run"          # NEW 2026-05-28 — manual cron triggers
P_STOREFRONT_CONFIGURE = "storefront.configure"  # NEW — nav, featured, static pages

# ----- Marketplace Fulfillment (2026-05-24) — 16 fine-grained perms -----
# Splits the blanket rider.dispatch into per-action gates so the
# dispatcher / rider_manager / support_lead / finance_manager roles
# each only get what they actually need. Cuts blast radius on
# accidental clicks (a support agent can notify customer but cannot
# approve refunds, etc.).
P_FF_QUEUE_VIEW = "fulfillment.queue.view"            # dispatch queue control
P_FF_PICKUP_ASSIGN = "fulfillment.pickup.assign"      # seller pickup
P_FF_DELIVERY_ASSIGN = "fulfillment.delivery.assign"  # customer delivery
P_FF_RIDER_REASSIGN = "fulfillment.rider.reassign"    # delay/failure fix
P_FF_BATCH_CREATE = "fulfillment.batch.create"        # route efficiency
P_FF_FAIL_MARK = "fulfillment.fail.mark"              # structured failure data
P_FF_RESCHEDULE = "fulfillment.reschedule"            # customer-requested time
P_FF_RETURN_HUB = "fulfillment.return.hub"            # failed/returned parcel
P_FF_RETURN_SELLER = "fulfillment.return.seller"      # seller return flow
P_FF_NOTIFY_CUSTOMER = "fulfillment.notify.customer"  # delivery updates
P_FF_ESCALATE_SUPPORT = "fulfillment.escalate.support"     # customer issue
P_FF_ESCALATE_FINANCE = "fulfillment.escalate.finance"     # COD / payment
P_FF_ESCALATE_MANAGER = "fulfillment.escalate.manager"     # high-risk case
P_FF_COD_EXPOSURE_VIEW = "fulfillment.cod.exposure.view"   # rider risk
P_FF_RIDER_CAPACITY_VIEW = "fulfillment.rider.capacity.view"  # assignment quality
P_FF_SLA_ALERTS_VIEW = "fulfillment.sla.alerts.view"   # late-order prevention

# ----- High-risk financial / destructive perms (2026-05-24) -----
# Each gates a sensitive write that can move money or destroy data.
# Role grants are deliberately tight — most are admin / super_admin
# only; the few that finance roles legitimately need (refund, COD,
# settlement) get scoped grants. Audit trail captures every call.
P_ORDER_CONFIRM = "order.confirm"                      # placed → confirmed
P_ORDER_CANCEL_HV = "order.cancel.high_value"          # cancel ≥৳10k order WITHOUT a 2nd approval (admin/super only)
P_PAYMENT_MARK_SUCCESS = "payment.mark.success"        # manual gateway success
P_REFUND_APPROVE = "refund.approve"                    # issue refund + wallet credit + seller debit
P_COD_EDIT = "cod.collected.edit"                      # edit rider-collected COD amount
P_COD_SETTLEMENT_APPROVE = "cod.settlement.approve"    # approve rider COD settlement bank transfer
P_RIDER_WALLET_EDIT = "rider.wallet.edit"              # manual ledger adjustment
P_SELLER_PAYOUT_EDIT = "seller.payout.edit"            # adjust supplier bill amount
P_PRODUCT_PRICE_EDIT = "product.price.edit"            # direct PDP price write
P_ORDER_DELETE = "order.delete"                        # destructive — super_admin only
P_AUDIT_DELETE = "audit.delete"                        # destructive — super_admin only (compliance risk)
P_DELIVERY_MARK_NO_PROOF = "delivery.mark.no_proof"    # mark delivered without rider proof (override)

# Delivery zones — split from catalog.product.write so read-only
# oversight roles (supervisor) + dispatch roles (dispatcher,
# rider_manager) can see zone config without product-catalog write.
P_DELIVERY_ZONE_READ = "delivery.zone.read"    # NEW — list zones + rate tiers
P_DELIVERY_ZONE_WRITE = "delivery.zone.write"  # NEW — create/update/delete zones

# Reports — alias for the existing reporting.admin perm
P_REPORTS_VIEW = "reports.view"        # alias for P_REPORTING_ADMIN

# Module runtime config (added 2026-05-16) — gates writes to the
# module_settings + module_feature_flags tables. Reads use iam.role.read
# (same perm that gates /admin/iam/roles, since module config exposes
# the privilege topology). Writes are restricted to admin + super_admin
# since flipping a flag can break customer flows.
P_MODULE_CONFIG_WRITE = "module.config.write"

# Supervisor + Last-Mile Manager (Phase D — wired 2026-05-29)
P_SUPERVISOR_ACTION = "supervisor.action"         # supervisor verbs (flag/escalate/review)
P_MANAGER_DECISION = "manager.decision"           # manager approve/reject pending row
P_SUPERVISOR_RISK_FLAG = "supervisor.risk_flag"   # flag seller/rider/customer risk
P_SUPERVISOR_VIEW = "supervisor.view"             # list approvals/escalations/risk-flags


# Registry-visibility perms (2026-05-29 — 15-panel role build). These
# match required_perm strings already referenced by fulfillment modules
# in admin_modules.py that previously had NO constant, so those modules
# were visible only to super_admin's "*". Declaring them lets the
# warehouse / logistics / risk panels actually see their modules.
P_PACKING_VIEW = "packing.view"
P_RIDER_ROUTING_WRITE = "rider.routing.write"
P_RETURNS_PROCESS = "returns.process"
P_FRAUD_ANALYTICS_VIEW = "fraud_analytics.view"
P_AUDIT_READ_SHORT = "audit.read"


ALL_PERMISSIONS: tuple[str, ...] = (
    P_USER_READ_SELF,
    P_USER_UPDATE_SELF,
    P_USER_READ_ANY,
    P_USER_UPDATE_ANY,
    P_USER_DELETE_ANY,
    P_USER_CREATE,
    P_USER_LIST,
    P_ROLE_READ,
    P_ROLE_ASSIGN,
    P_AUDIT_READ,
    P_SESSION_REVOKE_ANY,
    P_CATALOG_PRODUCT_READ,
    P_CATALOG_PRODUCT_WRITE,
    P_CATALOG_CATEGORY_WRITE,
    P_INVENTORY_READ,
    P_INVENTORY_RECEIVE,
    P_INVENTORY_ADJUST,
    P_INVENTORY_COUNT_APPROVE,
    P_CART_USE,
    P_ORDER_PLACE,
    P_ORDER_READ_SELF,
    P_ORDER_READ_ANY,
    P_ORDER_FULFILL,
    P_ORDER_CANCEL_ANY,
    P_PAYMENT_REFUND,
    P_PAYMENT_READ_ANY,
    P_FINANCE_READ,
    P_FINANCE_POST,
    P_FINANCE_SETTLE,
    P_FINANCE_CLOSE,
    P_FINANCE_ADJUST,
    P_DASHBOARD_READ,
    P_AI_USE,
    P_AI_READ,
    P_REPORTING_ADMIN,
    P_RIDER_APP,
    P_RIDER_DISPATCH,
    P_REVIEWS_WRITE,
    P_REVIEWS_ADMIN,
    P_SELLERS_READ,
    P_SELLERS_WRITE,
    P_SELLERS_ADMIN,
    P_FUNNEL_VIEW,
    P_FUNNEL_TRACK,
    P_FUNNEL_EXPORT,
    P_AFFILIATES_ADMIN,
    P_LOYALTY_ADMIN,
    P_REFERRALS_ADMIN,
    P_GIFT_CARDS_ADMIN,
    P_COUPONS_ADMIN,
    P_CC_AGENT,
    P_CC_ADMIN,
    P_CC_RAG_ADMIN,
    # Plural / short-form perms (2026-05-16)
    P_ORDERS_VIEW,
    P_ORDERS_UPDATE,
    P_ORDERS_CANCEL,
    P_PAYMENTS_VIEW,
    P_PAYMENTS_RECONCILE,
    P_RIDERS_COD_SETTLE,
    P_SELLERS_APPROVE,
    P_AI_CARE_VIEW,
    P_VOICE_CALL_ASSIGN,
    P_VOICE_CALL_VIEW,
    P_CC_INBOX_VIEW,
    P_CC_INBOX_REPLY,
    P_CC_INBOX_ADMIN,
    P_SEO_CONFIGURE,
    P_SEO_VIEW,
    P_SEO_BULK_PUBLISH,
    P_SEO_CRON_RUN,
    P_STOREFRONT_CONFIGURE,
    P_FF_QUEUE_VIEW,
    P_FF_PICKUP_ASSIGN,
    P_FF_DELIVERY_ASSIGN,
    P_FF_RIDER_REASSIGN,
    P_FF_BATCH_CREATE,
    P_FF_FAIL_MARK,
    P_FF_RESCHEDULE,
    P_FF_RETURN_HUB,
    P_FF_RETURN_SELLER,
    P_FF_NOTIFY_CUSTOMER,
    P_FF_ESCALATE_SUPPORT,
    P_FF_ESCALATE_FINANCE,
    P_FF_ESCALATE_MANAGER,
    P_FF_COD_EXPOSURE_VIEW,
    P_FF_RIDER_CAPACITY_VIEW,
    P_FF_SLA_ALERTS_VIEW,
    P_ORDER_CONFIRM,
    P_ORDER_CANCEL_HV,
    P_PAYMENT_MARK_SUCCESS,
    P_REFUND_APPROVE,
    P_COD_EDIT,
    P_COD_SETTLEMENT_APPROVE,
    P_RIDER_WALLET_EDIT,
    P_SELLER_PAYOUT_EDIT,
    P_PRODUCT_PRICE_EDIT,
    P_ORDER_DELETE,
    P_AUDIT_DELETE,
    P_DELIVERY_MARK_NO_PROOF,
    P_DELIVERY_ZONE_READ,
    P_DELIVERY_ZONE_WRITE,
    P_REPORTS_VIEW,
    P_MODULE_CONFIG_WRITE,
    # Supervisor + Last-Mile Manager (Phase D — wired 2026-05-29)
    P_SUPERVISOR_ACTION,
    P_MANAGER_DECISION,
    P_SUPERVISOR_RISK_FLAG,
    P_SUPERVISOR_VIEW,
    # Sponsored Products (2026-05-17)
    P_ADS_VIEW,
    P_ADS_MANAGE,
    # Couriers (2026-05-17)
    P_COURIERS_VIEW,
    P_COURIERS_MANAGE,
    # Cart recovery (2026-05-17)
    P_CART_RECOVERY_VIEW,
    P_CART_RECOVERY_MANAGE,
    # Disputes (2026-05-17)
    P_DISPUTES_VIEW,
    P_DISPUTES_MANAGE,
    P_DISPUTES_MEDIATE,
    # Customer segmentation (M6 — 2026-05-18)
    P_CUSTOMER_SEGMENTS_VIEW,
    P_CUSTOMER_SEGMENTS_MANAGE,
    # Seller rating (M5 — 2026-05-18)
    P_SELLER_RATINGS_VIEW,
    P_SELLER_RATINGS_MANAGE,
    # M7 bulk product upload (2026-05-18)
    P_BULK_UPLOAD_VIEW,
    P_BULK_UPLOAD_MANAGE,
    # Registry-visibility perms (2026-05-29 — 15-panel role build)
    P_PACKING_VIEW,
    P_RIDER_ROUTING_WRITE,
    P_RETURNS_PROCESS,
    P_FRAUD_ANALYTICS_VIEW,
    P_AUDIT_READ_SHORT,
)


@dataclass(frozen=True, slots=True)
class RoleSpec:
    name: str
    description: str
    permissions: tuple[str, ...]
    is_system: bool = True


# ---------- Role catalog ----------
ROLE_CUSTOMER = RoleSpec(
    name="customer",
    description="End-user buyer. Can manage own profile, cart, orders.",
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_CATALOG_PRODUCT_READ,
        P_CART_USE,
        P_ORDER_PLACE,
        P_ORDER_READ_SELF,
        P_REVIEWS_WRITE,
    ),
)

ROLE_STAFF = RoleSpec(
    name="staff",
    description="Front-line operations: read inventory, fulfill orders.",
    permissions=(
        P_USER_READ_ANY,
        P_USER_LIST,
        P_CATALOG_PRODUCT_READ,
        P_INVENTORY_READ,
        P_ORDER_READ_ANY,
        P_ORDER_FULFILL,
        # Short-form perms (2026-05-16)
        P_ORDERS_VIEW,
        P_ORDERS_UPDATE,
    ),
)

ROLE_MANAGER = RoleSpec(
    name="manager",
    description="Manages catalog, inventory, orders. No user-deletion or refund issuance.",
    permissions=(
        P_USER_READ_ANY,
        P_USER_LIST,
        P_USER_UPDATE_ANY,
        P_ROLE_READ,
        P_AUDIT_READ,
        P_CATALOG_PRODUCT_READ,
        P_CATALOG_PRODUCT_WRITE,
        P_CATALOG_CATEGORY_WRITE,
        P_INVENTORY_READ,
        P_INVENTORY_RECEIVE,
        P_INVENTORY_ADJUST,
        P_INVENTORY_COUNT_APPROVE,
        P_ORDER_READ_ANY,
        P_ORDER_FULFILL,
        P_ORDER_CANCEL_ANY,
        P_PAYMENT_READ_ANY,
        P_FINANCE_READ,
        P_DASHBOARD_READ,
        P_AI_USE,
        P_AI_READ,
        P_REPORTING_ADMIN,   # added 2026-05-16: keep formal name aligned with short-form P_REPORTS_VIEW
        P_REVIEWS_ADMIN,
        P_SELLERS_ADMIN,
        # Funnel dashboard — managers can VIEW KPI + fire test events,
        # but cannot run /retargeting/export (audience PII to ad
        # networks). That stays admin-only via the wildcard "*" on
        # ROLE_ADMIN below.
        P_FUNNEL_VIEW,
        P_FUNNEL_TRACK,
        # Short-form perms (2026-05-16) — manager covers everything
        # admin does for catalog/orders/sellers/reporting/seo work,
        # short of the destructive admin verbs.
        P_ORDERS_VIEW,
        P_ORDERS_UPDATE,
        P_ORDERS_CANCEL,
        P_PAYMENTS_VIEW,
        P_SELLERS_APPROVE,
        P_REPORTS_VIEW,
        P_SEO_CONFIGURE,
        P_SEO_VIEW,
        P_SEO_CRON_RUN,        # 2026-05-28 — manager may manually fire daily blog queue
        P_STOREFRONT_CONFIGURE,
        # Delivery zone management — managers run merchant ops.
        P_DELIVERY_ZONE_READ,
        P_DELIVERY_ZONE_WRITE,
        # Coupons admin — promo codes are merchant ops, not customer browsing.
        P_COUPONS_ADMIN,
        # Sponsored Products oversight (Phase 1.A — 2026-05-17). Manager
        # can pause/suspend any campaign + see invalid-click queue.
        P_ADS_VIEW,
        P_ADS_MANAGE,
        # Couriers (Phase M2.A — 2026-05-17). Manager configures provider
        # credentials + reads shipment status.
        P_COURIERS_VIEW,
        P_COURIERS_MANAGE,
        # Cart recovery (Phase M3.B — 2026-05-17). Manager runs the
        # abandoned-cart + win-back automation dashboard.
        P_CART_RECOVERY_VIEW,
        P_CART_RECOVERY_MANAGE,
        # Disputes (Phase M4 — 2026-05-17). Manager has full mediator
        # authority including final-decision verb.
        P_DISPUTES_VIEW,
        P_DISPUTES_MANAGE,
        P_DISPUTES_MEDIATE,
        # Customer segmentation (M6 — 2026-05-18). Manager runs cohort
        # analysis + audience exports for ad retargeting.
        P_CUSTOMER_SEGMENTS_VIEW,
        P_CUSTOMER_SEGMENTS_MANAGE,
        # Seller rating (M5 — 2026-05-18). Manager monitors quality
        # scores + can suspend a misbehaving seller.
        P_SELLER_RATINGS_VIEW,
        P_SELLER_RATINGS_MANAGE,
        # M7 bulk product upload (2026-05-18). Manager oversees seller
        # bulk ingest jobs + can force-retry failed jobs.
        P_BULK_UPLOAD_VIEW,
        P_BULK_UPLOAD_MANAGE,
        # CC unified inbox (migration 0073 — 2026-05-18). Manager owns
        # the admin-shell inbox + CSAT console alongside disputes.
        P_CC_INBOX_VIEW,
        P_CC_INBOX_REPLY,
        P_CC_INBOX_ADMIN,
        P_VOICE_CALL_VIEW,
    ),
)

ROLE_ADMIN = RoleSpec(
    name="admin",
    description=(
        "Operational admin. Manages all merchant ops, finance, growth, and "
        "content modules. Cannot delete users, assign roles, revoke "
        "sessions, ingest RAG corpus, or export funnel PII — those are "
        "super_admin-only break-glass actions."
    ),
    permissions=(
        # IAM (read + soft update only — no delete / role-assign / session-revoke)
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_USER_READ_ANY,
        P_USER_LIST,
        P_USER_UPDATE_ANY,
        P_ROLE_READ,
        P_AUDIT_READ,
        # Catalog + inventory full
        P_CATALOG_PRODUCT_READ,
        P_CATALOG_PRODUCT_WRITE,
        P_CATALOG_CATEGORY_WRITE,
        P_INVENTORY_READ,
        P_INVENTORY_RECEIVE,
        P_INVENTORY_ADJUST,
        P_INVENTORY_COUNT_APPROVE,
        # Orders + payments
        P_ORDER_READ_ANY,
        P_ORDER_FULFILL,
        P_ORDER_CANCEL_ANY,
        P_PAYMENT_REFUND,
        P_PAYMENT_READ_ANY,
        # Finance full (post, settle, close, adjust)
        P_FINANCE_READ,
        P_FINANCE_POST,
        P_FINANCE_SETTLE,
        P_FINANCE_CLOSE,
        P_FINANCE_ADJUST,
        # Dashboards + AI + reporting
        P_DASHBOARD_READ,
        P_AI_USE,
        P_AI_READ,
        P_REPORTING_ADMIN,
        # Rider dispatch (fleet ops)
        P_RIDER_DISPATCH,
        # Reviews + sellers admin
        P_REVIEWS_ADMIN,
        P_SELLERS_ADMIN,
        # Funnel view + track (NOT export — super_admin only for PII to ad nets)
        P_FUNNEL_VIEW,
        P_FUNNEL_TRACK,
        # Growth modules (loyalty / referrals / gift-cards / affiliates / coupons)
        P_AFFILIATES_ADMIN,
        P_LOYALTY_ADMIN,
        P_REFERRALS_ADMIN,
        P_GIFT_CARDS_ADMIN,
        P_COUPONS_ADMIN,
        # Customer-care admin (NOT rag.admin — knowledge-base destruct stays super_admin)
        P_CC_AGENT,
        P_CC_ADMIN,
        # Short-form perms (2026-05-16) — admin gets every short-form
        # capability except the destructive RAG/PII verbs reserved for
        # super_admin. ai_care.view + voice_call.assign here so admins
        # can triage customer-care from the admin shell without needing
        # the dedicated CC PWA.
        P_ORDERS_VIEW,
        P_ORDERS_UPDATE,
        P_ORDERS_CANCEL,
        P_PAYMENTS_VIEW,
        P_PAYMENTS_RECONCILE,
        P_RIDERS_COD_SETTLE,
        P_SELLERS_APPROVE,
        P_AI_CARE_VIEW,
        P_VOICE_CALL_ASSIGN,
        P_SEO_CONFIGURE,
        P_SEO_VIEW,
        P_STOREFRONT_CONFIGURE,
        P_DELIVERY_ZONE_READ,
        P_DELIVERY_ZONE_WRITE,
        P_REPORTS_VIEW,
        # Module runtime config (added 2026-05-16). Admin can flip
        # module_settings + module_feature_flags. super_admin gets it
        # via wildcard. No other role — flipping a flag can break
        # customer flows so we keep the surface narrow.
        P_MODULE_CONFIG_WRITE,
        # Supervisor + Last-Mile Manager (Phase D — wired 2026-05-29).
        # Admin gets full surface (action / decision / risk flag / view).
        P_SUPERVISOR_ACTION,
        P_MANAGER_DECISION,
        P_SUPERVISOR_RISK_FLAG,
        P_SUPERVISOR_VIEW,
        # Sponsored Products platform (Phase 1.A — 2026-05-17).
        P_ADS_VIEW,
        P_ADS_MANAGE,
        # Couriers — credential CRUD + shipment ops (Phase M2.A — 2026-05-17).
        P_COURIERS_VIEW,
        P_COURIERS_MANAGE,
        # Cart recovery — KPI dashboard + manual suppression CRUD.
        P_CART_RECOVERY_VIEW,
        P_CART_RECOVERY_MANAGE,
        # Disputes — full authority including mediator final decision.
        P_DISPUTES_VIEW,
        P_DISPUTES_MANAGE,
        P_DISPUTES_MEDIATE,
        # Customer segmentation (M6 — 2026-05-18). Full authority.
        P_CUSTOMER_SEGMENTS_VIEW,
        P_CUSTOMER_SEGMENTS_MANAGE,
        # Seller rating (M5 — 2026-05-18). Full authority including
        # admin override (suspend/unsuspend) + on-demand recompute.
        P_SELLER_RATINGS_VIEW,
        P_SELLER_RATINGS_MANAGE,
        # CC unified inbox (migration 0073 — 2026-05-18). Full authority
        # including agent assignment + spam marking.
        P_CC_INBOX_VIEW,
        P_CC_INBOX_REPLY,
        P_CC_INBOX_ADMIN,
        P_VOICE_CALL_VIEW,
    ),
)

# Break-glass owner / founder. Sole wildcard role. Use for the absolute
# top of the hierarchy (CEO / CTO / sysop). Anything sensitive that
# admin cannot do — delete users, assign roles, revoke sessions, ingest
# RAG corpus, export funnel PII to ad networks — flows through
# super_admin only.
ROLE_SUPER_ADMIN = RoleSpec(
    name="super_admin",
    description=(
        "Sole wildcard role. Owner / founder / break-glass operator. "
        "Has every permission including user-delete, role-assign, "
        "session-revoke, RAG knowledge-base ingest, and funnel PII "
        "export. Reserve for the smallest possible group."
    ),
    permissions=("*",),
)

# Read-only oversight role. For audit, compliance, and exec dashboards.
# Cannot mutate anything — every endpoint check that requires a write
# perm will fail for this role.
ROLE_SUPERVISOR = RoleSpec(
    name="supervisor",
    description=(
        "Read-only oversight. Audit + compliance + exec dashboards. "
        "Sees catalog, inventory, orders, payments, finance summaries, "
        "reviews, sellers, funnel KPI. Cannot mutate or settle anything."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_USER_READ_ANY,
        P_USER_LIST,
        P_ROLE_READ,
        P_AUDIT_READ,
        P_CATALOG_PRODUCT_READ,
        P_INVENTORY_READ,
        P_ORDER_READ_ANY,
        P_PAYMENT_READ_ANY,
        P_FINANCE_READ,
        P_DASHBOARD_READ,
        P_AI_READ,
        P_REPORTING_ADMIN,
        P_SELLERS_READ,
        P_FUNNEL_VIEW,
        # Short-form perms (2026-05-16) — supervisor is strictly
        # read-only, so only the *view perms; no update / cancel /
        # reconcile / settle / approve / configure / assign.
        P_ORDERS_VIEW,
        P_PAYMENTS_VIEW,
        P_REPORTS_VIEW,
        # Read-only zone visibility for audit / SLA oversight.
        P_DELIVERY_ZONE_READ,
        # Supervisor verbs (Phase D, wired 2026-05-29) — supervisor can
        # flag risk, open escalations, and start failed-delivery reviews;
        # manager-tier verbs (decide) require a separate role.
        P_SUPERVISOR_ACTION,
        P_SUPERVISOR_RISK_FLAG,
        P_SUPERVISOR_VIEW,
    ),
)

# Finance / accounting role. Owns posting, settlement, refunds, and
# period close. Cannot touch catalog, inventory, or seller config.
ROLE_FINANCE_MANAGER = RoleSpec(
    name="finance_manager",
    description=(
        "Finance + accounting owner. Posts journal entries, settles "
        "supplier bills, issues refunds, runs period close + adjustments, "
        "reviews COD wallet settlements. No catalog or merchant config."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_USER_READ_ANY,
        P_USER_LIST,
        P_AUDIT_READ,
        P_ORDER_READ_ANY,
        P_PAYMENT_REFUND,
        P_PAYMENT_READ_ANY,
        P_FINANCE_READ,
        P_FINANCE_POST,
        P_FINANCE_SETTLE,
        P_FINANCE_CLOSE,
        P_FINANCE_ADJUST,
        # Gift-card liability sits on the balance sheet — finance owns the
        # admin console for issuance / void / reconciliation.
        P_GIFT_CARDS_ADMIN,
        P_DASHBOARD_READ,
        P_REPORTING_ADMIN,
        P_AI_READ,
        # Short-form perms (2026-05-16) — finance owns gateway-side
        # reconciliation and rider-COD settlement (the cash handover
        # from rider to finance, distinct from MFS payout to the
        # rider's bank which already lives in P_FINANCE_SETTLE).
        P_ORDERS_VIEW,
        P_PAYMENTS_VIEW,
        P_PAYMENTS_RECONCILE,
        P_RIDERS_COD_SETTLE,
        P_REPORTS_VIEW,
    ),
)

# Front-line customer-support agent. Distinct from customercare_agent
# (Module 47 WhatsApp specialist) — support_agent is the generic ticket /
# call-center / order-help role used across all support channels.
ROLE_SUPPORT_AGENT = RoleSpec(
    name="support_agent",
    description=(
        "Generic customer-support agent. Reads any order to help "
        "customers, replies on the WhatsApp inbox, sees own profile. "
        "No moderation, no refund, no admin authority."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_USER_READ_ANY,
        P_USER_LIST,
        P_CATALOG_PRODUCT_READ,
        P_ORDER_READ_ANY,
        P_DASHBOARD_READ,
        P_CC_AGENT,
        # Short-form perms (2026-05-16) — support agent triages the
        # AI-flagged conversation queue and routes inbound voice calls
        # to the right specialist.
        P_ORDERS_VIEW,
        P_AI_CARE_VIEW,
        P_VOICE_CALL_ASSIGN,
        # Cart recovery — support reads the dispatch / suppression log when
        # a customer asks why they got / stopped getting reminders.
        P_CART_RECOVERY_VIEW,
        # Disputes — support reads disputes for customer assistance, and
        # acts as fallback mediator (spec allows when no customer_care_lead
        # role exists — grant mediate so an agent can decide simple cases).
        P_DISPUTES_VIEW,
        P_DISPUTES_MANAGE,
        P_DISPUTES_MEDIATE,
        # CC unified inbox (migration 0073 — 2026-05-18). Front-line
        # agents read + reply but cannot assign or close-as-spam.
        P_CC_INBOX_VIEW,
        P_CC_INBOX_REPLY,
        P_VOICE_CALL_VIEW,
    ),
)

# Marketplace ops role — onboarding, KYC moderation, commission config,
# review moderation. Sees orders for dispute support but cannot fulfill.
ROLE_SELLER_MANAGER = RoleSpec(
    name="seller_manager",
    description=(
        "Marketplace ops. Onboards sellers, runs KYC moderation, "
        "configures commission + payout, moderates reviews, manages "
        "catalog quality. Sees orders for dispute support."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_USER_READ_ANY,
        P_USER_LIST,
        P_CATALOG_PRODUCT_READ,
        P_CATALOG_PRODUCT_WRITE,
        P_CATALOG_CATEGORY_WRITE,
        P_ORDER_READ_ANY,
        P_DASHBOARD_READ,
        P_REPORTING_ADMIN,
        P_REVIEWS_ADMIN,
        P_SELLERS_ADMIN,
        # Short-form perms (2026-05-16) — seller_manager owns the
        # granular approve verb (split out of the broader sellers.admin)
        # plus edit-on-placed-order for dispute resolution.
        P_ORDERS_VIEW,
        P_ORDERS_UPDATE,
        P_SELLERS_APPROVE,
        P_REPORTS_VIEW,
        # Coupons admin — seller_manager configures coupons that promote
        # marketplace sellers' catalogs alongside marketing's own promos.
        P_COUPONS_ADMIN,
        # Sponsored Products — seller_manager oversees seller campaigns.
        P_ADS_VIEW,
        P_ADS_MANAGE,
        # M7 bulk product upload (2026-05-18). seller_manager oversees
        # the queue + can force-retry stuck seller jobs.
        P_BULK_UPLOAD_VIEW,
        P_BULK_UPLOAD_MANAGE,
        # Seller rating (M5 — 2026-05-18). Owns the score dashboard +
        # admin override (suspend/unsuspend) for misbehaving sellers.
        P_SELLER_RATINGS_VIEW,
        P_SELLER_RATINGS_MANAGE,
    ),
)

# Rider fleet manager. Strictly broader than ROLE_DISPATCHER — adds
# COD-wallet finance read + reporting admin. Owns the rider org as a
# whole rather than a single shift.
ROLE_RIDER_MANAGER = RoleSpec(
    name="rider_manager",
    description=(
        "Rider fleet manager. Owns the dispatch console, run-sheet "
        "overrides, COD wallet review (read-only finance), and rider "
        "performance reporting. Cannot settle MFS payouts (that's "
        "finance_manager) or hire/fire (that's super_admin)."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_USER_READ_ANY,
        P_USER_LIST,
        P_AUDIT_READ,
        P_ORDER_READ_ANY,
        P_RIDER_DISPATCH,
        P_FINANCE_READ,
        P_DASHBOARD_READ,
        P_REPORTING_ADMIN,
        # Short-form perms (2026-05-16) — rider_manager records the
        # cash handover from rider to dispatch desk; the downstream
        # MFS payout to the rider's bank stays with finance_manager
        # via P_FINANCE_SETTLE.
        P_ORDERS_VIEW,
        P_RIDERS_COD_SETTLE,
        P_REPORTS_VIEW,
        # Zone visibility for dispatch / SLA decisions; not write
        # (rate negotiation stays with admin/manager).
        P_DELIVERY_ZONE_READ,
        # Couriers — rider_manager reads shipment / tracking status for
        # fleet SLA oversight. Credential CRUD stays admin/manager.
        P_COURIERS_VIEW,
        # Supervisor + Last-Mile Manager (Phase D — wired 2026-05-29).
        # rider_manager is one of two manager-tier roles that can finalise
        # pending supervisor approvals (RIDER_REASSIGNMENT etc.).
        P_MANAGER_DECISION,
        P_SUPERVISOR_ACTION,
        P_SUPERVISOR_RISK_FLAG,
        P_SUPERVISOR_VIEW,
    ),
)

# Growth / marketing role. Owns funnel KPI, retargeting export, AI
# campaign tooling, and reporting. Read-only on catalog + orders.
ROLE_MARKETING_MANAGER = RoleSpec(
    name="marketing_manager",
    description=(
        "Growth + marketing owner. Reads funnel KPI, exports "
        "retargeting audiences to Meta/Google/TikTok, runs loyalty + "
        "referrals + affiliates, uses AI campaign tooling. Sole "
        "non-super_admin role with funnel.export."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_USER_READ_ANY,
        P_USER_LIST,
        P_CATALOG_PRODUCT_READ,
        P_ORDER_READ_ANY,
        P_DASHBOARD_READ,
        P_REPORTING_ADMIN,
        P_AI_USE,
        P_AI_READ,
        P_FUNNEL_VIEW,
        P_FUNNEL_TRACK,
        P_FUNNEL_EXPORT,
        # Growth tools that drive marketing campaigns (loyalty programs,
        # referral codes, affiliate payouts, coupons). Gift-cards stays
        # with finance — issuance is a balance-sheet entry, not a campaign.
        P_AFFILIATES_ADMIN,
        P_LOYALTY_ADMIN,
        P_REFERRALS_ADMIN,
        P_COUPONS_ADMIN,
        # Short-form perms (2026-05-16) — SEO config + reporting.
        # orders.view added later 2026-05-16 when /admin/orders gate
        # swapped from order.read.any to orders.view (canonical short-form).
        P_SEO_CONFIGURE,
        P_SEO_VIEW,
        P_SEO_BULK_PUBLISH,    # 2026-05-28 — admin-only: seeds 50k+ programmatic pages
        P_SEO_CRON_RUN,        # 2026-05-28 — manual SEO cron trigger
        P_STOREFRONT_CONFIGURE,
        P_REPORTS_VIEW,
        P_ORDERS_VIEW,
        # Sponsored Products — marketing_manager runs platform-wide ad
        # promotions alongside coupons / loyalty / affiliates.
        P_ADS_VIEW,
        P_ADS_MANAGE,
        # Cart recovery — marketing owns abandoned-cart + win-back automation.
        P_CART_RECOVERY_VIEW,
        P_CART_RECOVERY_MANAGE,
        # Customer segmentation (M6 — 2026-05-18). Marketing owns audience
        # CSV exports for Meta/Google/TikTok retargeting + segment CRUD.
        P_CUSTOMER_SEGMENTS_VIEW,
        P_CUSTOMER_SEGMENTS_MANAGE,
    ),
)

# AI platform owner. Manages the RAG knowledge base (ingest / reindex /
# delete), trains assistive models, audits AI proposals across modules.
# Distinct from individual AI consumers (managers can use AI; ai_manager
# administers the platform).
ROLE_AI_MANAGER = RoleSpec(
    name="ai_manager",
    description=(
        "AI platform owner. Ingests + reindexes the RAG knowledge "
        "base, audits AI proposals across modules (OCR, suggest, "
        "fraud, predict), and tunes assistive models. Read-only on "
        "catalog + orders for context — no merchant or finance authority."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_USER_READ_ANY,
        P_USER_LIST,
        P_AUDIT_READ,
        P_CATALOG_PRODUCT_READ,
        P_ORDER_READ_ANY,
        P_AI_USE,
        P_AI_READ,
        P_CC_RAG_ADMIN,
        P_DASHBOARD_READ,
        P_REPORTING_ADMIN,
        # Short-form perms (2026-05-16) — ai_manager reads the AI-flagged
        # care triage queue (ai_care.view) to audit how the assistive
        # models score conversations. No voice_call.assign — that
        # routing decision belongs to support, not the AI platform team.
        # orders.view added later 2026-05-16 when /admin/orders gate
        # swapped from order.read.any to orders.view (canonical short-form).
        P_AI_CARE_VIEW,
        P_REPORTS_VIEW,
        P_ORDERS_VIEW,
    ),
)

# Mobile-app delivery rider. Mounted to a Rider row via
# Rider.linked_user_id; wallet + run-sheet endpoints filter by that link.
ROLE_RIDER = RoleSpec(
    name="rider",
    description=(
        "Delivery rider on the mobile app. Self-serve shift + run sheet + "
        "wallet endpoints; cannot read other riders' data."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_RIDER_APP,
        P_ORDER_READ_SELF,
        # ``order.fulfill`` is what gates the rider mobile-app's
        # delivery endpoints (pickup / upload-pod / deliver / fail /
        # scan / upload-signature). Without it the rider can't mark
        # anything delivered. Added 2026-05-13 after the COD E2E
        # test confirmed the rider role was missing this perm.
        P_ORDER_FULFILL,
    ),
)

# Ops dispatcher / fleet supervisor. Sees the whole rider fleet,
# manages run sheets, reviews COD wallet settlements.
ROLE_DISPATCHER = RoleSpec(
    name="dispatcher",
    description=(
        "Dispatch + fleet ops. Manages run sheets, overrides next-stop, "
        "reviews rider COD wallets + MFS settlements. No finance "
        "settle/close authority."
    ),
    permissions=(
        P_USER_READ_ANY,
        P_USER_LIST,
        P_ORDER_READ_ANY,
        # ``order.fulfill`` (2026-05-17) — dispatcher physically moves
        # orders into ``out_for_delivery``. The same perm also gates
        # ``/start-packing`` and ``/complete``; dispatchers in practice
        # only call ``/dispatch`` but the perm catalog doesn't split
        # them today. If a future audit requires tighter scoping,
        # split P_ORDER_FULFILL into P_ORDER_PACK / P_ORDER_DISPATCH /
        # P_ORDER_COMPLETE and grant just P_ORDER_DISPATCH here.
        P_ORDER_FULFILL,
        P_RIDER_DISPATCH,
        P_DASHBOARD_READ,
        P_REPORTING_ADMIN,
        # Short-form perms (2026-05-16) — dispatcher works a single
        # shift, so only the read verbs (no settle / approve / configure).
        P_ORDERS_VIEW,
        P_REPORTS_VIEW,
        # Zone read is essential — dispatcher routes by zone tier + ETA.
        P_DELIVERY_ZONE_READ,
    ),
)

# Marketplace seller (sellers phase 3) — granted to user accounts
# linked to an approved seller via the ``seller_users`` table. The
# seller-scoped endpoints under ``/api/v1/seller/*`` enforce per-row
# isolation in addition to this role check.
ROLE_SELLER = RoleSpec(
    name="seller",
    description=(
        "Marketplace seller — owner / manager / staff of an approved "
        "seller account. Can read own seller profile, own catalog, "
        "own orders, own videos. Cannot see any other seller's data."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_CATALOG_PRODUCT_READ,
        P_CATALOG_PRODUCT_WRITE,  # gated per-row by sellers.authz at service layer
        # Sponsored Products — seller manages own ad wallet + campaigns
        # under /api/v1/seller/ads/*. Per-row isolation in service layer.
        P_ADS_MANAGE,
        P_SELLERS_READ,
        P_SELLERS_WRITE,
        P_ORDER_READ_SELF,        # placeholder; phase 3 lookup is by seller_id, not customer_user_id
    ),
)

# Customer-care agent (Module 47). Granted to support staff. Cannot
# moderate users/orders directly — operates only on conversations,
# their own assigned customers, and the CC follow-up queue.
ROLE_CUSTOMERCARE_AGENT = RoleSpec(
    name="customercare_agent",
    description=(
        "AI WhatsApp customer-care agent. Handles inbound conversations, "
        "sends replies, manages own follow-ups. Read-only on catalog "
        "+ orders. No admin or RAG-ingest authority."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_CATALOG_PRODUCT_READ,
        P_ORDER_READ_ANY,        # can look up any customer's order to help them
        P_CC_AGENT,
        # Short-form perms (2026-05-16) — agents triage AI-flagged
        # conversations. Voice-call routing stays with supervisors.
        P_ORDERS_VIEW,
        P_AI_CARE_VIEW,
    ),
)

# Customer-care supervisor / admin. Adds RAG-ingest + SLA/follow-up
# config + agent-management on top of the agent permission set.
ROLE_CUSTOMERCARE_ADMIN = RoleSpec(
    name="customercare_admin",
    description=(
        "Customer-care supervisor. Manages agents, SLA policies, "
        "follow-up campaigns, and the RAG knowledge base."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_USER_READ_ANY,
        P_USER_LIST,
        P_CATALOG_PRODUCT_READ,
        P_ORDER_READ_ANY,
        P_CC_AGENT,
        P_CC_ADMIN,
        P_CC_RAG_ADMIN,
        # Short-form perms (2026-05-16) — CC supervisor triages the
        # AI queue AND routes inbound voice calls to specialists.
        P_ORDERS_VIEW,
        P_AI_CARE_VIEW,
        P_VOICE_CALL_ASSIGN,
        # Disputes — CC supervisor mediates customer disputes (the
        # closest existing role to a "customer_care_lead" who would
        # otherwise own dispute resolution end-to-end).
        P_DISPUTES_VIEW,
        P_DISPUTES_MANAGE,
        P_DISPUTES_MEDIATE,
        # CC unified inbox (migration 0073 — 2026-05-18).
        P_CC_INBOX_VIEW,
        P_CC_INBOX_REPLY,
        P_CC_INBOX_ADMIN,
        P_VOICE_CALL_VIEW,
    ),
)


# ===================================================================
# 15-panel responsibility split (2026-05-29). Panels 1/3/4/8/9/10/11
# reuse existing roles (super_admin, finance_manager, marketing_manager,
# rider_manager, customercare_admin, support_agent, seller_manager).
# The 8 roles below are the new ones. Module visibility is driven purely
# by required_perm in admin_modules.py — see each role's note for the
# modules its perm set unlocks via /admin/config/me.
# ===================================================================

# Panel 2 — Director / C-Level. Read-only big picture. No operational
# buttons. Sees KPI + finance-read + audit surfaces only.
ROLE_DIRECTOR = RoleSpec(
    name="director",
    description=(
        "Director / C-level. Read-only cross-department KPI + finance "
        "summary + audit visibility. No operational write anywhere."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_DASHBOARD_READ,      # dashboard, analytics, widgets-board
        P_REPORTS_VIEW,        # reporting
        P_FINANCE_READ,        # finance, supplier-payments, tax-rules, subscriptions
        P_AUDIT_READ,          # audit-log
    ),
)

# Panel 5 — Catalog Manager. Product hierarchy, content quality, search
# relevance. Owns taxonomy + moderation + media.
ROLE_CATALOG_MANAGER = RoleSpec(
    name="catalog_manager",
    description=(
        "Catalog owner. Product/category taxonomy, bulk upload, review "
        "+ Q&A + video moderation, SKU pricing, search relevance, SEO read."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_CATALOG_PRODUCT_READ,
        P_CATALOG_PRODUCT_WRITE,   # catalog, product-videos, live-shopping, search-index
        P_CATALOG_CATEGORY_WRITE,
        P_PRODUCT_PRICE_EDIT,
        P_REVIEWS_ADMIN,           # reviews, product-qa, returns, fraud, support-agent
        P_BULK_UPLOAD_VIEW,
        P_BULK_UPLOAD_MANAGE,      # bulk_upload
        P_SEO_VIEW,                # seo-audit, seo-domination
        P_DASHBOARD_READ,          # dashboard, analytics, widgets-board
    ),
)

# Panel 6 — Order Manager. Order lifecycle confirm -> cancel/refund,
# dispute kickoff, payment view.
ROLE_ORDER_MANAGER = RoleSpec(
    name="order_manager",
    description=(
        "Order lifecycle owner. Confirms/cancels/refunds orders, mediates "
        "disputes, reads payments. No catalog or finance posting."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_ORDER_READ_ANY,
        P_ORDERS_VIEW,             # orders, fulfillment-ready-to-ship, fulfillment-failed-delivery
        P_ORDERS_UPDATE,
        P_ORDERS_CANCEL,
        P_ORDER_CONFIRM,
        P_ORDER_CANCEL_ANY,
        P_REFUND_APPROVE,
        P_PAYMENT_REFUND,
        P_PAYMENT_READ_ANY,
        P_PAYMENTS_VIEW,           # payments
        P_REVIEWS_ADMIN,           # returns, reviews, product-qa, fraud, support-agent
        P_DISPUTES_VIEW,
        P_DISPUTES_MEDIATE,        # disputes
        P_DASHBOARD_READ,          # dashboard, analytics, widgets-board
    ),
)

# Panel 7 — Warehouse Manager. In-warehouse ops GRN -> dispatch.
ROLE_WAREHOUSE_MANAGER = RoleSpec(
    name="warehouse_manager",
    description=(
        "In-warehouse ops owner. Receiving, putaway, picks, packs, "
        "dispatch batches, cycle counts, hub sorting. No money actions."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_INVENTORY_READ,
        P_INVENTORY_RECEIVE,
        P_INVENTORY_ADJUST,
        P_INVENTORY_COUNT_APPROVE,  # inventory
        P_ORDER_FULFILL,            # packing, deliveries
        P_PACKING_VIEW,             # fulfillment-hub-sorting
        P_BULK_UPLOAD_VIEW,         # bulk_upload
        P_COURIERS_VIEW,            # couriers
        P_DASHBOARD_READ,           # dashboard, analytics, widgets-board
    ),
)

# Panel 12 — Risk / Fraud Officer. Risk surface, block bad actors,
# chargeback defence, audit forensics.
ROLE_RISK_OFFICER = RoleSpec(
    name="risk_officer",
    description=(
        "Risk + fraud owner. Fraud queue, COD-risk monitor, dispute "
        "mediation, audit-log forensics. Read user/seller for investigation."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_USER_LIST,                # sellers, users-iam (investigation)
        P_REVIEWS_ADMIN,            # fraud, returns, reviews, product-qa, support-agent
        P_FRAUD_ANALYTICS_VIEW,     # fulfillment-cod-risk
        P_AUDIT_READ,               # audit-log
        P_AUDIT_READ_SHORT,         # fulfillment-audit-logs
        P_DISPUTES_VIEW,
        P_DISPUTES_MEDIATE,         # disputes
        P_DASHBOARD_READ,           # dashboard, analytics, widgets-board
    ),
)

# Panel 13 — Content / SEO Specialist. Storefront polish, CMS, meta.
ROLE_CONTENT_MANAGER = RoleSpec(
    name="content_manager",
    description=(
        "Content + SEO owner. Storefront CMS, SEO config/audit/domination, "
        "media + review moderation, reporting read."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_SEO_CONFIGURE,            # seo
        P_SEO_VIEW,                 # seo-audit, seo-domination
        P_SEO_BULK_PUBLISH,
        P_STOREFRONT_CONFIGURE,     # storefront
        P_CATALOG_PRODUCT_WRITE,    # product-videos (media moderation) + catalog, search-index, live-shopping
        P_REVIEWS_ADMIN,            # reviews, product-qa, returns, fraud, support-agent
        P_REPORTS_VIEW,             # reporting
        P_DASHBOARD_READ,           # dashboard, analytics, widgets-board
    ),
)

# Panel 14 — Data / BI Analyst. Read deep, no write, build reports.
ROLE_DATA_ANALYST = RoleSpec(
    name="data_analyst",
    description=(
        "Pure-read analyst. Reporting, dashboards, funnel + segments. "
        "No write anywhere."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_REPORTS_VIEW,             # reporting
        P_DASHBOARD_READ,           # dashboard, analytics, widgets-board
        P_FUNNEL_VIEW,
        P_FUNNEL_TRACK,             # funnel, marketing
        P_FUNNEL_EXPORT,
        P_CUSTOMER_SEGMENTS_VIEW,   # customer_segments
    ),
)

# Panel 15 — System Administrator. IAM + infra + automation. Non-finance
# super-power. NOTE: feature-flags stays super_admin-only ("*" gate) by
# design — system_admin manages roles/users/audit/module-config, not flags.
ROLE_SYSTEM_ADMIN = RoleSpec(
    name="system_admin",
    description=(
        "System / IAM admin. Role assignments, user management, audit "
        "forensics, module config. No finance. Not the break-glass owner."
    ),
    permissions=(
        P_USER_READ_SELF,
        P_USER_UPDATE_SELF,
        P_USER_READ_ANY,
        P_USER_UPDATE_ANY,
        P_USER_DELETE_ANY,
        P_USER_CREATE,              # create internal users (POST /admin/iam/users)
        P_USER_LIST,                # users-iam, sellers
        P_ROLE_READ,
        P_ROLE_ASSIGN,              # roles, module-config
        P_SESSION_REVOKE_ANY,
        P_MODULE_CONFIG_WRITE,
        P_AUDIT_READ,               # audit-log
        P_AUDIT_READ_SHORT,         # fulfillment-audit-logs
        P_DASHBOARD_READ,           # dashboard, analytics, widgets-board
    ),
)


ALL_ROLES: tuple[RoleSpec, ...] = (
    # Customer-facing
    ROLE_CUSTOMER,
    # Internal ops (legacy 4-tier)
    ROLE_STAFF, ROLE_MANAGER, ROLE_ADMIN,
    # Break-glass owner — sole wildcard
    ROLE_SUPER_ADMIN,
    # Functional managers (added 2026-05-16 — 8-role expansion)
    ROLE_SUPERVISOR,
    ROLE_FINANCE_MANAGER,
    ROLE_SUPPORT_AGENT,
    ROLE_SELLER_MANAGER,
    ROLE_RIDER_MANAGER,
    ROLE_MARKETING_MANAGER,
    ROLE_AI_MANAGER,
    # Mobile / marketplace / customer-care
    ROLE_RIDER, ROLE_DISPATCHER, ROLE_SELLER,
    ROLE_CUSTOMERCARE_AGENT, ROLE_CUSTOMERCARE_ADMIN,
    # 15-panel responsibility split (2026-05-29 — 8 new roles)
    ROLE_DIRECTOR,
    ROLE_CATALOG_MANAGER,
    ROLE_ORDER_MANAGER,
    ROLE_WAREHOUSE_MANAGER,
    ROLE_RISK_OFFICER,
    ROLE_CONTENT_MANAGER,
    ROLE_DATA_ANALYST,
    ROLE_SYSTEM_ADMIN,
)
DEFAULT_ROLE_FOR_NEW_USERS: str = ROLE_CUSTOMER.name


def role_by_name(name: str) -> RoleSpec | None:
    for r in ALL_ROLES:
        if r.name == name:
            return r
    return None
