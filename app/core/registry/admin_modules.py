"""Declarative catalog of every admin module that has a real backend
endpoint AND a frontend page. One row per page in the admin shell nav.

Schema:
  code         — stable slug, used as nav React `key`
  label_en/_bn — display text per locale
  group        — sidebar group (Merchant ops, Finance, Growth, etc.)
  href         — admin SPA route (without locale prefix)
  required_perm — permission string a user needs to see this entry.
                  Admin role's wildcard `*` matches anything; specific
                  roles match the literal string.
  version      — bump when the page's API contract changes (FE can
                  warn if it's older than what the page client expects)
  notes        — short internal description for ops handoff
  enabled      — runtime flag. Set False to hide a row across all roles
                 (e.g. live-shopping while backend is parked).
  api_prefix   — backend route root (e.g. /api/v1/orders) — surfaced via
                 /admin/module-registry so the FE shell can prefetch /
                 ping the relevant API without hardcoding URLs.
  icon         — Lucide / Heroicons name for the FE sidebar glyph.
  order        — sort index within the group. Lower = higher in sidebar.
                 Defaults to position in this tuple, so leaving it 0
                 keeps the catalog-declaration order.
  menu         — optional sub-nav items. Empty = single nav entry built
                 from (label_en, href, required_perm).

Filtering:
  ``visible_modules_for(perms)`` returns the subset whose
  required_perm is satisfied by the caller's permission set.
  ``*`` in caller's perms returns every enabled module.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AdminMenuItem:
    label: str
    path: str
    permission: str


@dataclass(frozen=True)
class AdminModule:
    code: str
    label_en: str
    label_bn: str
    group: str
    href: str
    required_perm: str
    version: int = 1
    notes: str = ""
    enabled: bool = True
    api_prefix: str | None = None
    icon: str | None = None
    order: int = 0
    menu: tuple[AdminMenuItem, ...] = field(default_factory=tuple)


# Enum-like surface for grouping in the FE sidebar. Order matters —
# this is the rendering order.
class AdminModuleGroup:
    OVERVIEW = "Overview"
    MERCHANT_OPS = "Merchant ops"
    OPS_QUEUE = "Ops queue"
    WAREHOUSE = "Warehouse + delivery"
    FULFILLMENT = "Marketplace Fulfillment"  # 2026-05-24 — unified ops view
    FINANCE = "Finance"
    GROWTH = "Growth"
    CONTENT = "Content + SEO"
    SYSTEM = "System"


# The single source of truth. Order = sidebar order within each group.
# All entries below populate api_prefix/icon/order (2026-05-16, later
# session) so the /admin/module-registry response is self-describing for
# FE shell rendering — no hardcoded module metadata on the FE side.
ADMIN_MODULES: tuple[AdminModule, ...] = (
    # ─── Overview ───────────────────────────────────────────────
    AdminModule(
        code="dashboard",
        label_en="Dashboard",
        label_bn="ড্যাশবোর্ড",
        group=AdminModuleGroup.OVERVIEW,
        href="/admin/dashboard",
        required_perm="dashboard.read",
        api_prefix="/api/v1/admin/dashboard",
        icon="LayoutDashboard",
        order=10,
    ),
    AdminModule(
        code="analytics",
        label_en="Analytics",
        label_bn="অ্যানালিটিক্স",
        group=AdminModuleGroup.OVERVIEW,
        href="/admin/analytics",
        required_perm="dashboard.read",
        api_prefix="/api/v1/admin/analytics",
        icon="BarChart3",
        order=20,
    ),

    # ─── Merchant ops ───────────────────────────────────────────
    AdminModule(
        code="sellers",
        label_en="Sellers",
        label_bn="সেলার",
        group=AdminModuleGroup.MERCHANT_OPS,
        href="/admin/sellers",
        required_perm="iam.user.list",
        api_prefix="/api/v1/admin/sellers",
        icon="Store",
        order=10,
    ),
    AdminModule(
        code="catalog",
        label_en="Catalog",
        label_bn="ক্যাটালগ",
        group=AdminModuleGroup.MERCHANT_OPS,
        href="/admin/catalog",
        # Tightened 2026-05-16: was `catalog.product.read` (every
        # browsing customer satisfied it, which leaked the admin module
        # into customer / seller / customercare visibility). Now gated
        # on the write perm so only roles that can edit catalog see it.
        required_perm="catalog.product.write",
        api_prefix="/api/v1/catalog",
        icon="Package",
        order=20,
    ),
    AdminModule(
        code="orders",
        label_en="Orders",
        label_bn="অর্ডার",
        group=AdminModuleGroup.MERCHANT_OPS,
        href="/admin/orders",
        required_perm="orders.view",  # synced to endpoint gate 2026-05-16 (was order.read.any)
        api_prefix="/api/v1/orders",
        icon="ShoppingCart",
        order=30,
    ),

    # ─── Ops queue (moderation surfaces) ────────────────────────
    AdminModule(
        code="returns",
        label_en="Returns (RMA)",
        label_bn="রিটার্ন",
        group=AdminModuleGroup.OPS_QUEUE,
        href="/admin/returns-v2",
        required_perm="reviews.admin",  # reuses ops perm; staff.fulfill cannot
        notes="Customer return queue (receive/inspect/complete/reject)",
        api_prefix="/api/v1/returns",
        icon="PackageX",
        order=10,
    ),
    AdminModule(
        code="reviews",
        label_en="Reviews moderation",
        label_bn="রিভিউ মডারেশন",
        group=AdminModuleGroup.OPS_QUEUE,
        href="/admin/reviews-v2",
        required_perm="reviews.admin",
        api_prefix="/api/v1/reviews",
        icon="MessageSquare",
        order=20,
    ),
    AdminModule(
        code="product-qa",
        label_en="Product Q&A",
        label_bn="পণ্য Q&A",
        group=AdminModuleGroup.OPS_QUEUE,
        href="/admin/product-qa",
        required_perm="reviews.admin",
        api_prefix="/api/v1/admin/qa",
        icon="HelpCircle",
        order=30,
    ),
    AdminModule(
        code="product-videos",
        label_en="Product videos",
        label_bn="পণ্যের ভিডিও",
        group=AdminModuleGroup.OPS_QUEUE,
        href="/admin/product-videos-v2",
        required_perm="catalog.product.write",
        api_prefix="/api/v1/admin/product-videos",
        icon="Video",
        order=40,
    ),
    AdminModule(
        code="fraud",
        label_en="Fraud queue",
        label_bn="ফ্রড কিউ",
        group=AdminModuleGroup.OPS_QUEUE,
        href="/admin/fraud-v2",
        required_perm="reviews.admin",
        api_prefix="/api/v1/admin/fraud",
        icon="ShieldAlert",
        order=50,
    ),
    AdminModule(
        code="support-agent",
        label_en="Support (agent)",
        label_bn="সহায়তা (এজেন্ট)",
        group=AdminModuleGroup.OPS_QUEUE,
        href="/admin/support",
        required_perm="reviews.admin",
        notes="Cross-customer ticket queue, agent reply",
        api_prefix="/api/v1/admin/support",
        icon="Headphones",
        order=60,
    ),
    AdminModule(
        code="voice-calls",
        label_en="Voice calls",
        label_bn="ভয়েস কল",
        group=AdminModuleGroup.OPS_QUEUE,
        href="/admin/voice-calls",
        required_perm="voice_call.assign",
        notes="Inbound voice-call dispatch + softphone (Banglalink HUB)",
        api_prefix="/api/v1/customer-care/voice-calls",
        icon="Phone",
        order=70,
    ),
    AdminModule(
        code="voice-ai",
        label_en="Voice AI Agent",
        label_bn="ভয়েস AI এজেন্ট",
        group=AdminModuleGroup.OPS_QUEUE,
        href="/admin/voice-ai",
        required_perm="customercare.agent",
        notes="Inbound AI voice registration: telephony adapter + ASR + AI reply + TTS + SIP softphone",
        api_prefix="/api/v1/customer-care",
        icon="PhoneCall",
        order=71,
    ),

    # ─── Warehouse + delivery ───────────────────────────────────
    AdminModule(
        code="inventory",
        label_en="Inventory",
        label_bn="ইনভেন্টরি",
        group=AdminModuleGroup.WAREHOUSE,
        href="/admin/inventory",
        required_perm="inventory.read",
        api_prefix="/api/v1/admin/inventory",
        icon="Boxes",
        order=10,
    ),
    AdminModule(
        code="packing",
        label_en="Packing",
        label_bn="প্যাকিং",
        group=AdminModuleGroup.WAREHOUSE,
        href="/admin/packing",
        required_perm="order.fulfill",
        api_prefix="/api/v1/admin/packing",
        icon="PackageOpen",
        order=20,
    ),
    AdminModule(
        code="deliveries",
        label_en="Deliveries",
        label_bn="ডেলিভারি",
        group=AdminModuleGroup.WAREHOUSE,
        href="/admin/deliveries",
        required_perm="order.fulfill",
        api_prefix="/api/v1/admin/deliveries",
        icon="Truck",
        order=30,
    ),
    AdminModule(
        code="rider-routing",
        label_en="Rider routing",
        label_bn="রাইডার রাউটিং",
        group=AdminModuleGroup.WAREHOUSE,
        href="/admin/rider-routing",
        required_perm="rider.dispatch",
        api_prefix="/api/v1/admin/rider-routing",
        icon="Map",
        order=40,
    ),
    AdminModule(
        code="couriers",
        label_en="Couriers",
        label_bn="কুরিয়ার",
        group=AdminModuleGroup.WAREHOUSE,
        href="/admin/couriers",
        required_perm="couriers.view",
        notes="External courier integrations (Pathao, RedX, Sundarban, Steadfast)",
        api_prefix="/api/v1/admin/couriers",
        icon="Truck",
        order=50,
    ),

    # ─── Finance ────────────────────────────────────────────────
    AdminModule(
        code="finance",
        label_en="Finance",
        label_bn="ফাইন্যান্স",
        group=AdminModuleGroup.FINANCE,
        href="/admin/finance",
        required_perm="finance.read",
        api_prefix="/api/v1/admin/finance",
        icon="Calculator",
        order=10,
    ),
    AdminModule(
        code="payments",
        label_en="Payments",
        label_bn="পেমেন্ট",
        group=AdminModuleGroup.FINANCE,
        href="/admin/payments",
        required_perm="payments.view",  # synced to endpoint gate 2026-05-16 (was finance.read)
        api_prefix="/api/v1/payments",
        icon="CreditCard",
        order=20,
    ),
    AdminModule(
        code="supplier-payments",
        label_en="Supplier payments",
        label_bn="সাপ্লায়ার পেমেন্ট",
        group=AdminModuleGroup.FINANCE,
        href="/admin/supplier-payments",
        required_perm="finance.read",
        api_prefix="/api/v1/admin/supplier-payments",
        icon="FileText",
        order=30,
    ),
    AdminModule(
        code="tax-rules",
        label_en="Tax rules",
        label_bn="ট্যাক্স নিয়ম",
        group=AdminModuleGroup.FINANCE,
        href="/admin/tax-rules",
        required_perm="finance.read",
        api_prefix="/api/v1/admin/pricing/tax-rules",
        icon="Receipt",
        order=40,
    ),

    # ─── Growth ─────────────────────────────────────────────────
    AdminModule(
        code="coupons",
        label_en="Coupons",
        label_bn="কুপন",
        group=AdminModuleGroup.GROWTH,
        href="/admin/coupons",
        # Tightened 2026-05-16: was `catalog.product.read` ("broad —
        # staff sees too" admitted the leak). Now gated on the new
        # `coupons.admin` perm scoped to admin / manager /
        # marketing_manager / seller_manager.
        required_perm="coupons.admin",
        api_prefix="/api/v1/coupons",
        icon="Ticket",
        order=10,
    ),
    AdminModule(
        code="affiliates",
        label_en="Affiliates",
        label_bn="অ্যাফিলিয়েট",
        group=AdminModuleGroup.GROWTH,
        href="/admin/affiliates",
        required_perm="affiliates.admin",
        api_prefix="/api/v1/affiliates",
        icon="Users",
        order=20,
    ),
    AdminModule(
        code="loyalty",
        label_en="Loyalty",
        label_bn="লয়াল্টি",
        group=AdminModuleGroup.GROWTH,
        href="/admin/loyalty",
        required_perm="loyalty.admin",
        api_prefix="/api/v1/loyalty",
        icon="Award",
        order=30,
    ),
    AdminModule(
        code="referrals",
        label_en="Referrals",
        label_bn="রেফারেল",
        group=AdminModuleGroup.GROWTH,
        href="/admin/referrals",
        required_perm="referrals.admin",
        api_prefix="/api/v1/referrals",
        icon="UserPlus",
        order=40,
    ),
    AdminModule(
        code="gift-cards",
        label_en="Gift cards",
        label_bn="গিফট কার্ড",
        group=AdminModuleGroup.GROWTH,
        href="/admin/gift-cards",
        required_perm="gift_cards.admin",
        api_prefix="/api/v1/gift-cards",
        icon="Gift",
        order=50,
    ),
    AdminModule(
        code="funnel",
        label_en="Funnel + segments",
        label_bn="ফানেল + সেগমেন্ট",
        group=AdminModuleGroup.GROWTH,
        href="/admin/funnel",
        required_perm="funnel.track",
        api_prefix="/api/v1/admin/funnel",
        icon="Filter",
        order=60,
    ),
    AdminModule(
        code="marketing",
        label_en="Marketing",
        label_bn="মার্কেটিং",
        group=AdminModuleGroup.GROWTH,
        href="/admin/marketing-v2",
        required_perm="funnel.track",
        api_prefix="/api/v1/admin/marketing",
        icon="Megaphone",
        order=70,
    ),
    AdminModule(
        code="social-ads-geo",
        label_en="Social Ads (Geo)",
        label_bn="সোশ্যাল অ্যাডস (Geo)",
        group=AdminModuleGroup.GROWTH,
        href="/admin/social-ads-geo",
        required_perm="funnel.export",
        notes="Geo-targeted social campaigns (Meta/Google/TikTok/IMO) + consent-filtered audience export",
        api_prefix="/api/v1/funnel/retargeting",
        icon="Target",
        order=72,
    ),
    AdminModule(
        code="subscriptions",
        label_en="Subscriptions",
        label_bn="সাবস্ক্রিপশন",
        group=AdminModuleGroup.GROWTH,
        href="/admin/subscriptions",
        required_perm="finance.read",
        api_prefix="/api/v1/subscriptions",
        icon="RefreshCw",
        order=80,
    ),
    AdminModule(
        code="ads",
        label_en="Sponsored Products",
        label_bn="স্পনসর্ড প্রোডাক্ট",
        group=AdminModuleGroup.GROWTH,
        href="/admin/ads",
        required_perm="ads.view",
        notes="Sponsored Products advertising platform",
        api_prefix="/api/v1/admin/ads",
        icon="Megaphone",
        order=85,
    ),
    AdminModule(
        code="cart_recovery",
        label_en="Cart Recovery",
        label_bn="কার্ট পুনরুদ্ধার",
        group=AdminModuleGroup.GROWTH,
        href="/admin/cart-recovery",
        required_perm="cart_recovery.view",
        notes="Abandoned-cart reminders + win-back automation dashboard",
        api_prefix="/api/v1/admin/cart-recovery",
        icon="Repeat2",
        order=87,
    ),
    AdminModule(
        code="customer_segments",
        label_en="Customer Segments",
        label_bn="গ্রাহক সেগমেন্ট",
        group=AdminModuleGroup.GROWTH,
        href="/admin/customer-segments",
        required_perm="customer_segments.view",
        notes="RFM cohorts + named segments (VIP/dormant/new) + audience export CSV",
        api_prefix="/api/v1/admin/customer-segments",
        icon="Users",
        order=88,
    ),
    AdminModule(
        code="disputes",
        label_en="Disputes",
        label_bn="বিরোধ",
        group=AdminModuleGroup.OPS_QUEUE,
        href="/admin/disputes",
        required_perm="disputes.view",
        notes="Buyer/seller/mediator dispute resolution + escrow holds",
        api_prefix="/api/v1/admin/disputes",
        icon="Scale",
        order=88,
    ),
    AdminModule(
        code="seller_ratings",
        label_en="Seller Ratings",
        label_bn="সেলার রেটিং",
        group=AdminModuleGroup.MERCHANT_OPS,
        href="/admin/seller-ratings",
        required_perm="seller_ratings.view",
        notes="Per-seller quality score (0-100) + tier + history snapshots",
        api_prefix="/api/v1/admin/seller-ratings",
        icon="Star",
        order=86,
    ),
    AdminModule(
        code="bulk_upload",
        label_en="Bulk Upload",
        label_bn="বাল্ক আপলোড",
        group=AdminModuleGroup.MERCHANT_OPS,
        href="/admin/bulk-upload",
        required_perm="bulk_upload.view",
        notes="Seller CSV/XLSX bulk product ingest jobs + error-row queue",
        api_prefix="/api/v1/admin/bulk-upload",
        icon="Upload",
        order=89,
    ),
    AdminModule(
        code="cc_inbox",
        label_en="Customer Care Inbox",
        label_bn="কাস্টমার কেয়ার ইনবক্স",
        group=AdminModuleGroup.OPS_QUEUE,
        href="/admin/cc-inbox",
        required_perm="cc_inbox.view",
        notes="WhatsApp inbox + voice calls + CSAT — unified customer care console",
        api_prefix="/api/v1/admin/cc",
        icon="MessageSquare",
        order=89,
    ),
    AdminModule(
        code="whatsapp-ai",
        label_en="WhatsApp AI Agent",
        label_bn="WhatsApp AI এজেন্ট",
        group=AdminModuleGroup.OPS_QUEUE,
        href="/admin/whatsapp-ai",
        required_perm="customercare.agent",
        notes="WhatsApp Cloud API + AI auto-reply registration + live connection status",
        api_prefix="/api/v1/customer-care",
        icon="Bot",
        order=90,
    ),
    AdminModule(
        code="live-shopping",
        label_en="Live shopping",
        label_bn="লাইভ শপিং",
        group=AdminModuleGroup.GROWTH,
        href="/admin/live-shopping",
        required_perm="catalog.product.write",
        enabled=False,
        notes="Backend live_admin_router parked per app/main.py:401-405",
        api_prefix="/api/v1/admin/live-streams",
        icon="Radio",
        order=90,
    ),

    # ─── Content + SEO ──────────────────────────────────────────
    AdminModule(
        code="seo",
        label_en="SEO + content",
        label_bn="SEO + কনটেন্ট",
        group=AdminModuleGroup.CONTENT,
        href="/admin/seo-v2",
        required_perm="seo.configure",  # synced to endpoint gate 2026-05-16 (was catalog.category.write)
        api_prefix="/api/v1/admin/seo",
        icon="Search",
        order=10,
    ),
    AdminModule(
        code="storefront",
        label_en="Storefront control",
        label_bn="স্টোরফ্রন্ট কন্ট্রোল",
        group=AdminModuleGroup.CONTENT,
        href="/admin/storefront",
        required_perm="storefront.configure",
        api_prefix="/api/v1/admin/storefront",
        icon="LayoutGrid",
        order=11,
    ),
    AdminModule(
        code="seo-audit",
        label_en="SEO audit",
        label_bn="SEO অডিট",
        group=AdminModuleGroup.CONTENT,
        href="/admin/seo-audit",
        required_perm="seo.view",
        api_prefix="/api/v1/admin/seo/audit",
        icon="ListChecks",
        order=12,
    ),
    AdminModule(
        code="seo-domination",
        label_en="SEO Domination",
        label_bn="SEO ডমিনেশন",
        group=AdminModuleGroup.CONTENT,
        href="/admin/seo-domination",
        required_perm="seo.view",
        api_prefix="/api/v1/admin/seo-domination",
        icon="Trophy",
        order=13,
    ),
    AdminModule(
        code="reporting",
        label_en="Reporting",
        label_bn="রিপোর্টিং",
        group=AdminModuleGroup.CONTENT,
        href="/admin/reporting",
        required_perm="reports.view",  # synced to endpoint gate 2026-05-16 (was reporting.admin)
        api_prefix="/api/v1/admin/reporting",
        icon="FileBarChart",
        order=20,
    ),

    # ─── System ─────────────────────────────────────────────────
    AdminModule(
        code="search-index",
        label_en="Search index",
        label_bn="সার্চ ইনডেক্স",
        group=AdminModuleGroup.SYSTEM,
        href="/admin/search-v2",
        required_perm="catalog.product.write",
        api_prefix="/api/v1/admin/search",
        icon="Database",
        order=10,
    ),
    AdminModule(
        code="feature-flags",
        label_en="Feature flags",
        label_bn="ফিচার ফ্ল্যাগ",
        group=AdminModuleGroup.SYSTEM,
        href="/admin/feature-flags-v2",
        required_perm="*",  # super_admin-only (sole wildcard role since 2026-05-16)
        api_prefix="/api/v1/admin/feature-flags",
        icon="Flag",
        order=20,
    ),
    AdminModule(
        code="users-iam",
        label_en="Users (IAM)",
        label_bn="ইউজার (IAM)",
        group=AdminModuleGroup.SYSTEM,
        href="/admin/users-iam",
        required_perm="iam.user.list",
        notes="List/search users + assign/revoke roles",
        api_prefix="/api/v1/admin/users",
        icon="UserCog",
        order=30,
    ),
    AdminModule(
        code="roles",
        label_en="Roles & permissions",
        label_bn="রোল ও পারমিশন",
        group=AdminModuleGroup.SYSTEM,
        href="/admin/roles",
        required_perm="iam.role.read",
        notes="Read-only RBAC catalog + perm matrix",
        api_prefix="/api/v1/admin/iam",
        icon="Shield",
        order=40,
    ),
    AdminModule(
        code="audit-log",
        label_en="Audit log",
        label_bn="অডিট লগ",
        group=AdminModuleGroup.SYSTEM,
        href="/admin/audit-log",
        required_perm="iam.audit.read",
        notes="Filtered audit_logs viewer for compliance",
        api_prefix="/api/v1/admin/audit-log",
        icon="ScrollText",
        order=50,
    ),
    AdminModule(
        code="module-config",
        label_en="Module config",
        label_bn="মডিউল কনফিগ",
        group=AdminModuleGroup.SYSTEM,
        href="/admin/module-config",
        required_perm="iam.role.read",  # read-tier; writes need module.config.write
        notes="Per-module runtime settings + feature flags",
        api_prefix="/api/v1/admin/modules",
        icon="Settings",
        order=60,
    ),
    AdminModule(
        code="widgets-board",
        label_en="Widgets board",
        label_bn="উইজেট বোর্ড",
        group=AdminModuleGroup.OVERVIEW,
        href="/admin/widgets-board",
        required_perm="dashboard.read",
        notes="Composable dashboard with 8 widget types + per-user layout",
        api_prefix="/api/v1/admin/dashboard",
        icon="LayoutGrid",
        order=30,
    ),

    # ─── Marketplace Fulfillment ────────────────────────────────
    # Unified ops view — 16 surfaces covering pickup → dispatch →
    # delivery → returns. The hub entry below is the canonical entry
    # point (`/admin/fulfillment`); the 16 sub-entries are sidebar
    # aliases that deep-link into either the hub tab or the existing
    # admin page.
    AdminModule(
        code="fulfillment-hub",
        label_en="Fulfillment Hub",
        label_bn="ফুলফিলমেন্ট হাব",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/fulfillment",
        required_perm="rider.dispatch",
        api_prefix="/api/v1/admin/fulfillment",
        icon="LayoutGrid",
        order=1,
        notes="Single-URL shell with 16 tabs covering pickup → dispatch → delivery → returns",
    ),
    AdminModule(
        code="fulfillment-dispatch-dashboard",
        label_en="Dispatch Dashboard",
        label_bn="ডিসপ্যাচ ড্যাশবোর্ড",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/rider-routing",
        required_perm="rider.dispatch",
        api_prefix="/api/v1/admin/rider-routing",
        icon="LayoutDashboard",
        order=10,
    ),
    AdminModule(
        code="fulfillment-ready-to-ship",
        label_en="Ready to Ship Orders",
        label_bn="শিপ করার জন্য প্রস্তুত",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/orders?status=ready_to_ship",
        required_perm="orders.view",
        api_prefix="/api/v1/admin/orders",
        icon="Package",
        order=20,
    ),
    AdminModule(
        code="fulfillment-seller-pickup",
        label_en="Seller Pickup Queue",
        label_bn="বিক্রেতা পিকআপ কিউ",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/fulfillment/seller-pickup",
        required_perm="rider.dispatch",
        api_prefix="/api/v1/admin/fulfillment/seller-pickup",
        icon="Truck",
        order=30,
        notes="Phase 2 build — new endpoint + admin client",
    ),
    AdminModule(
        code="fulfillment-hub-sorting",
        label_en="Hub Sorting",
        label_bn="হাব সর্টিং",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/packing",
        required_perm="packing.view",
        api_prefix="/api/v1/admin/packing",
        icon="Box",
        order=40,
    ),
    AdminModule(
        code="fulfillment-rider-assignment",
        label_en="Rider Assignment",
        label_bn="রাইডার অ্যাসাইনমেন্ট",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/rider-routing#assignment",
        required_perm="rider.routing.write",
        api_prefix="/api/v1/admin/rider-routing",
        icon="UserCheck",
        order=50,
    ),
    AdminModule(
        code="fulfillment-live-delivery",
        label_en="Live Delivery Monitor",
        label_bn="লাইভ ডেলিভারি মনিটর",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/rider-routing#live",
        required_perm="rider.dispatch",
        api_prefix="/api/v1/admin/rider-routing/live",
        icon="Activity",
        order=60,
    ),
    AdminModule(
        code="fulfillment-failed-delivery",
        label_en="Failed Delivery",
        label_bn="ফেইল্ড ডেলিভারি",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/deliveries?status=failed",
        required_perm="orders.view",
        api_prefix="/api/v1/admin/deliveries",
        icon="AlertTriangle",
        order=70,
    ),
    AdminModule(
        code="fulfillment-reschedule",
        label_en="Reschedule Queue",
        label_bn="রিশিডিউল কিউ",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/fulfillment/reschedule",
        required_perm="rider.routing.write",
        api_prefix="/api/v1/admin/fulfillment/reschedule",
        icon="Calendar",
        order=80,
        notes="Phase 2 build — new endpoint + admin client",
    ),
    AdminModule(
        code="fulfillment-return-hub",
        label_en="Return to Hub",
        label_bn="হাব এ রিটার্ন",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/returns-v2?leg=to_hub",
        required_perm="returns.process",
        api_prefix="/api/v1/admin/returns",
        icon="ArrowLeft",
        order=90,
    ),
    AdminModule(
        code="fulfillment-return-seller",
        label_en="Return to Seller",
        label_bn="বিক্রেতাকে রিটার্ন",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/returns-v2?leg=to_seller",
        required_perm="returns.process",
        api_prefix="/api/v1/admin/returns",
        icon="RotateCcw",
        order=100,
    ),
    AdminModule(
        code="fulfillment-cod-risk",
        label_en="COD Risk Monitor",
        label_bn="COD রিস্ক মনিটর",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/fraud-analytics?lens=cod",
        required_perm="fraud_analytics.view",
        api_prefix="/api/v1/admin/fraud-analytics",
        icon="ShieldAlert",
        order=110,
    ),
    AdminModule(
        code="fulfillment-sla-alerts",
        label_en="SLA Breach Alerts",
        label_bn="SLA ব্রিচ অ্যালার্ট",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/fulfillment/sla-alerts",
        required_perm="rider.dispatch",
        api_prefix="/api/v1/admin/fulfillment/sla-alerts",
        icon="Bell",
        order=120,
        notes="Phase 2 build — new endpoint + admin client",
    ),
    AdminModule(
        code="fulfillment-rider-capacity",
        label_en="Rider Capacity",
        label_bn="রাইডার ক্যাপাসিটি",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/rider-routing#capacity",
        required_perm="rider.dispatch",
        api_prefix="/api/v1/admin/rider-routing/capacity",
        icon="Gauge",
        order=130,
    ),
    AdminModule(
        code="fulfillment-seller-delay",
        label_en="Seller Delay Monitor",
        label_bn="বিক্রেতা বিলম্ব মনিটর",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/fulfillment/seller-delay",
        required_perm="sellers.approve",
        api_prefix="/api/v1/admin/fulfillment/seller-delay",
        icon="Clock",
        order=140,
        notes="Phase 2 build — new endpoint + admin client",
    ),
    AdminModule(
        code="fulfillment-notifications",
        label_en="Customer Notification Triggers",
        label_bn="কাস্টমার নোটিফিকেশন ট্রিগার",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/cart-recovery",
        required_perm="cc_inbox.admin",
        api_prefix="/api/v1/admin/cart-recovery",
        icon="MessageSquare",
        order=150,
    ),
    AdminModule(
        code="fulfillment-ops-console",
        label_en="Ops Console (money actions)",
        label_bn="অপস কনসোল (টাকার অ্যাকশন)",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/fulfillment/ops",
        required_perm="rider.dispatch",
        api_prefix="/api/v1/admin/fulfillment/ops",
        icon="DollarSign",
        order=155,
        notes="7 modal-driven actions: confirm/payment/refund/payout/wallet/price/dispute",
    ),
    AdminModule(
        code="fulfillment-audit-logs",
        label_en="Dispatch Audit Logs",
        label_bn="ডিসপ্যাচ অডিট লগ",
        group=AdminModuleGroup.FULFILLMENT,
        href="/admin/audit-log?resource=delivery",
        required_perm="audit.read",
        api_prefix="/api/v1/admin/audit-log",
        icon="FileText",
        order=160,
    ),
)


def _has_perm(needed: str, owned: Iterable[str]) -> bool:
    """`*` wildcard in caller's perms matches anything. Otherwise match
    the exact permission string."""
    owned_set = set(owned)
    if "*" in owned_set:
        return True
    return needed in owned_set


def visible_modules_for(perms: Iterable[str]) -> list[AdminModule]:
    """Filter ADMIN_MODULES to the subset the caller can see."""
    return [
        m
        for m in ADMIN_MODULES
        if m.enabled and _has_perm(m.required_perm, perms)
    ]
