"""Snapshot the admin-module visibility for every role in the catalog.

This test is the safety net for the 17-role expansion (2026-05-16). When
either side moves — a new admin module lands in
``app/core/registry/admin_modules.py`` or a role's permission set
changes in ``app/modules/iam/permissions.py`` — every affected snapshot
in this file fails loudly so the change is intentional, not silent.

Pure-function test: no DB, no fixtures. Runs in milliseconds.

When updating: re-run this test, copy the printed `actual` value back
into the EXPECTED_VISIBILITY dict, and re-run to confirm green. Don't
just delete the assertion — the snapshot is the contract.
"""

from __future__ import annotations

from app.core.registry.admin_modules import ADMIN_MODULES, visible_modules_for
from app.modules.iam.permissions import ALL_ROLES, role_by_name


# Sorted module-code lists per role. Update in lockstep with
# admin_modules.py / permissions.py edits — a diff here is the canary
# that catches accidental privilege creep / loss.
EXPECTED_VISIBILITY: dict[str, list[str]] = {
    # Snapshot refreshed 2026-05-29 (15-panel build). Lists are the
    # contract: a diff here = intentional role/registry change. super_admin
    # stays dynamic (= every enabled module).
    "customer": [],
    "staff": ["deliveries", "fulfillment-failed-delivery", "fulfillment-ready-to-ship", "inventory", "orders", "packing", "sellers", "users-iam"],
    "manager": ["ads", "analytics", "audit-log", "bulk_upload", "cart_recovery", "catalog", "cc_inbox", "coupons", "couriers", "customer_segments", "dashboard", "deliveries", "disputes", "finance", "fraud", "fulfillment-failed-delivery", "fulfillment-notifications", "fulfillment-ready-to-ship", "fulfillment-seller-delay", "funnel", "inventory", "marketing", "module-config", "orders", "packing", "payments", "product-qa", "product-videos", "reporting", "returns", "reviews", "roles", "search-index", "seller_ratings", "sellers", "seo", "seo-audit", "seo-domination", "storefront", "subscriptions", "supplier-payments", "support-agent", "tax-rules", "users-iam", "widgets-board"],
    "admin": ["ads", "affiliates", "analytics", "audit-log", "cart_recovery", "catalog", "cc_inbox", "coupons", "couriers", "customer_segments", "dashboard", "deliveries", "disputes", "finance", "fraud", "fulfillment-dispatch-dashboard", "fulfillment-failed-delivery", "fulfillment-hub", "fulfillment-live-delivery", "fulfillment-notifications", "fulfillment-ops-console", "fulfillment-ready-to-ship", "fulfillment-rider-capacity", "fulfillment-seller-delay", "fulfillment-seller-pickup", "fulfillment-sla-alerts", "funnel", "gift-cards", "inventory", "loyalty", "marketing", "module-config", "orders", "packing", "payments", "product-qa", "product-videos", "referrals", "reporting", "returns", "reviews", "rider-routing", "roles", "search-index", "seller_ratings", "sellers", "seo", "seo-audit", "seo-domination", "storefront", "subscriptions", "supplier-payments", "support-agent", "tax-rules", "users-iam", "voice-calls", "widgets-board"],
    "super_admin": sorted({m.code for m in ADMIN_MODULES if m.enabled}),
    "supervisor": ["analytics", "audit-log", "dashboard", "finance", "fulfillment-failed-delivery", "fulfillment-ready-to-ship", "inventory", "module-config", "orders", "payments", "reporting", "roles", "sellers", "subscriptions", "supplier-payments", "tax-rules", "users-iam", "widgets-board"],
    "finance_manager": ["analytics", "audit-log", "dashboard", "finance", "fulfillment-failed-delivery", "fulfillment-ready-to-ship", "gift-cards", "orders", "payments", "reporting", "sellers", "subscriptions", "supplier-payments", "tax-rules", "users-iam", "widgets-board"],
    "support_agent": ["analytics", "cart_recovery", "cc_inbox", "dashboard", "disputes", "fulfillment-failed-delivery", "fulfillment-ready-to-ship", "orders", "sellers", "users-iam", "voice-calls", "widgets-board"],
    "seller_manager": ["ads", "analytics", "bulk_upload", "catalog", "coupons", "dashboard", "fraud", "fulfillment-failed-delivery", "fulfillment-ready-to-ship", "fulfillment-seller-delay", "orders", "product-qa", "product-videos", "reporting", "returns", "reviews", "search-index", "seller_ratings", "sellers", "support-agent", "users-iam", "widgets-board"],
    "rider_manager": ["analytics", "audit-log", "couriers", "dashboard", "finance", "fulfillment-dispatch-dashboard", "fulfillment-failed-delivery", "fulfillment-hub", "fulfillment-live-delivery", "fulfillment-ops-console", "fulfillment-ready-to-ship", "fulfillment-rider-capacity", "fulfillment-seller-pickup", "fulfillment-sla-alerts", "orders", "reporting", "rider-routing", "sellers", "subscriptions", "supplier-payments", "tax-rules", "users-iam", "widgets-board"],
    "marketing_manager": ["ads", "affiliates", "analytics", "cart_recovery", "coupons", "customer_segments", "dashboard", "fulfillment-failed-delivery", "fulfillment-ready-to-ship", "funnel", "loyalty", "marketing", "orders", "referrals", "reporting", "sellers", "seo", "seo-audit", "seo-domination", "storefront", "users-iam", "widgets-board"],
    "ai_manager": ["analytics", "audit-log", "dashboard", "fulfillment-failed-delivery", "fulfillment-ready-to-ship", "orders", "reporting", "sellers", "users-iam", "widgets-board"],
    "rider": ["deliveries", "packing"],
    "dispatcher": ["analytics", "dashboard", "deliveries", "fulfillment-dispatch-dashboard", "fulfillment-failed-delivery", "fulfillment-hub", "fulfillment-live-delivery", "fulfillment-ops-console", "fulfillment-ready-to-ship", "fulfillment-rider-capacity", "fulfillment-seller-pickup", "fulfillment-sla-alerts", "orders", "packing", "reporting", "rider-routing", "sellers", "users-iam", "widgets-board"],
    "seller": ["catalog", "product-videos", "search-index"],
    "customercare_agent": ["fulfillment-failed-delivery", "fulfillment-ready-to-ship", "orders"],
    "customercare_admin": ["cc_inbox", "disputes", "fulfillment-failed-delivery", "fulfillment-notifications", "fulfillment-ready-to-ship", "orders", "sellers", "users-iam", "voice-calls"],
    "director": ["analytics", "audit-log", "dashboard", "finance", "reporting", "subscriptions", "supplier-payments", "tax-rules", "widgets-board"],
    "catalog_manager": ["analytics", "bulk_upload", "catalog", "dashboard", "fraud", "product-qa", "product-videos", "returns", "reviews", "search-index", "seo-audit", "seo-domination", "support-agent", "widgets-board"],
    "order_manager": ["analytics", "dashboard", "disputes", "fraud", "fulfillment-failed-delivery", "fulfillment-ready-to-ship", "orders", "payments", "product-qa", "returns", "reviews", "support-agent", "widgets-board"],
    "warehouse_manager": ["analytics", "bulk_upload", "couriers", "dashboard", "deliveries", "fulfillment-hub-sorting", "inventory", "packing", "widgets-board"],
    "risk_officer": ["analytics", "audit-log", "dashboard", "disputes", "fraud", "fulfillment-audit-logs", "fulfillment-cod-risk", "product-qa", "returns", "reviews", "sellers", "support-agent", "users-iam", "widgets-board"],
    "content_manager": ["analytics", "catalog", "dashboard", "fraud", "product-qa", "product-videos", "reporting", "returns", "reviews", "search-index", "seo", "seo-audit", "seo-domination", "storefront", "support-agent", "widgets-board"],
    "data_analyst": ["analytics", "customer_segments", "dashboard", "funnel", "marketing", "reporting", "widgets-board"],
    "system_admin": ["analytics", "audit-log", "dashboard", "fulfillment-audit-logs", "module-config", "roles", "sellers", "users-iam", "widgets-board"],
}


def _visible_codes(role_name: str) -> list[str]:
    role = role_by_name(role_name)
    assert role is not None, f"Role {role_name!r} missing from ALL_ROLES"
    return sorted({m.code for m in visible_modules_for(role.permissions)})


def test_every_role_in_catalog_has_a_snapshot() -> None:
    """Catalog and snapshot dict must agree — neither side may grow alone."""
    catalog_names = {r.name for r in ALL_ROLES}
    snapshot_names = set(EXPECTED_VISIBILITY)
    missing = catalog_names - snapshot_names
    extra = snapshot_names - catalog_names
    assert not missing, (
        f"Roles in ALL_ROLES but missing from EXPECTED_VISIBILITY: {sorted(missing)}. "
        "Add a snapshot entry."
    )
    assert not extra, (
        f"EXPECTED_VISIBILITY has roles not in ALL_ROLES: {sorted(extra)}. "
        "Remove the dead snapshot entry."
    )


def test_role_module_visibility_matches_snapshot() -> None:
    """Per-role snapshot — fails loudly when a role's reachable module set drifts."""
    actual = {name: _visible_codes(name) for name in EXPECTED_VISIBILITY}
    diffs = {
        name: {"expected": EXPECTED_VISIBILITY[name], "actual": actual[name]}
        for name in EXPECTED_VISIBILITY
        if EXPECTED_VISIBILITY[name] != actual[name]
    }
    assert not diffs, (
        "Role module visibility drifted. Review each diff and either fix "
        "the role's permissions / the registry, OR update EXPECTED_VISIBILITY "
        "if the change is intentional.\n\n"
        f"Drifts: {diffs}"
    )


def test_super_admin_sees_every_enabled_module() -> None:
    """Wildcard invariant: super_admin's view = every enabled module, no exceptions."""
    super_admin = role_by_name("super_admin")
    assert super_admin is not None
    assert super_admin.permissions == ("*",), (
        "super_admin must hold the sole wildcard. If you scoped it, "
        "the rest of the test suite is now wrong."
    )
    visible = {m.code for m in visible_modules_for(super_admin.permissions)}
    enabled = {m.code for m in ADMIN_MODULES if m.enabled}
    assert visible == enabled


def test_admin_lost_wildcard_and_thus_lost_feature_flags() -> None:
    """Regression guard: when admin was wildcard it saw feature-flags too.

    After the 2026-05-16 expansion, feature-flags' ``required_perm="*"``
    is satisfied only by super_admin. If admin shows up as visible to
    feature-flags again, it means somebody re-wildcarded admin —
    which would silently undo the principle-of-least-privilege model.
    """
    admin = role_by_name("admin")
    assert admin is not None
    assert "*" not in admin.permissions, (
        "admin must NOT hold wildcard — that's super_admin's job."
    )
    codes = {m.code for m in visible_modules_for(admin.permissions)}
    assert "feature-flags" not in codes


def test_customer_facing_roles_never_see_finance_or_admin_only_modules() -> None:
    """customer / seller / rider / customercare_* may see catalog-tier
    modules (because their browsing perms satisfy `catalog.product.read`),
    but they MUST NOT see finance, payments, settlement, supplier-payments,
    rider-routing, or anything wildcard-gated. The actual admin-shell
    barrier is FE-side (AdminAuthGate); this test guards the worst-case
    leaks the registry could enable.
    """
    forbidden = {
        "finance", "payments", "supplier-payments", "rider-routing",
        "feature-flags", "reporting", "loyalty", "affiliates", "referrals",
        "gift-cards", "marketing", "fraud",
        # Tightened 2026-05-16 — `coupons` admin module now requires the
        # new coupons.admin perm. Marketplace seller legitimately has
        # catalog.product.write (manages own catalog from the admin
        # shell), so `catalog` is NOT in the forbidden set.
        "coupons",
    }
    for name in ("customer", "seller", "rider", "customercare_agent", "customercare_admin"):
        role = role_by_name(name)
        assert role is not None
        codes = {m.code for m in visible_modules_for(role.permissions)}
        leaked = codes & forbidden
        assert not leaked, (
            f"{name} can see sensitive admin modules via permission catalog: {sorted(leaked)}. "
            "Either tighten the role's permissions or change the module's required_perm."
        )
