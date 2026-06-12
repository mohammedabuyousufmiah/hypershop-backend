"""Smoke tests for supervisor_lm router wiring (2026-05-29 wire-up).

Verifies:
  * Router is importable and has exactly 10 routes.
  * 4 new perms (P_SUPERVISOR_ACTION / P_MANAGER_DECISION /
    P_SUPERVISOR_RISK_FLAG / P_SUPERVISOR_VIEW) live in ALL_PERMISSIONS.
  * Supervisor + rider_manager + admin role grants are wired.
  * Service entry points (record_supervisor_action / record_manager_decision /
    create_*) are importable.
"""
from __future__ import annotations


def test_router_has_ten_routes() -> None:
    from app.modules.supervisor_lm import supervisor_lm_router

    assert len(supervisor_lm_router.routes) == 10
    paths = {r.path for r in supervisor_lm_router.routes}
    expected = {
        "/admin/supervisor/actions",
        "/admin/supervisor/escalations",
        "/admin/supervisor/risk-flags",
        "/admin/supervisor/failed-delivery-reviews",
        "/admin/supervisor/manager-approvals",
        "/admin/supervisor/manager-approvals/{approval_id}/decide",
    }
    assert expected.issubset(paths)


def test_perms_in_catalog() -> None:
    from app.modules.iam.permissions import (
        ALL_PERMISSIONS,
        P_MANAGER_DECISION,
        P_SUPERVISOR_ACTION,
        P_SUPERVISOR_RISK_FLAG,
        P_SUPERVISOR_VIEW,
    )

    for p in (
        P_SUPERVISOR_ACTION,
        P_MANAGER_DECISION,
        P_SUPERVISOR_RISK_FLAG,
        P_SUPERVISOR_VIEW,
    ):
        assert p in ALL_PERMISSIONS


def test_role_grants_wired() -> None:
    from app.modules.iam.permissions import (
        P_MANAGER_DECISION,
        P_SUPERVISOR_ACTION,
        P_SUPERVISOR_RISK_FLAG,
        P_SUPERVISOR_VIEW,
        ROLE_ADMIN,
        ROLE_RIDER_MANAGER,
        ROLE_SUPERVISOR,
    )

    # supervisor: action + risk_flag + view (no manager decision)
    sup_perms = set(ROLE_SUPERVISOR.permissions)
    assert P_SUPERVISOR_ACTION in sup_perms
    assert P_SUPERVISOR_RISK_FLAG in sup_perms
    assert P_SUPERVISOR_VIEW in sup_perms
    assert P_MANAGER_DECISION not in sup_perms

    # rider_manager: full surface
    rm_perms = set(ROLE_RIDER_MANAGER.permissions)
    assert P_MANAGER_DECISION in rm_perms
    assert P_SUPERVISOR_ACTION in rm_perms

    # admin: full surface
    adm_perms = set(ROLE_ADMIN.permissions)
    assert P_MANAGER_DECISION in adm_perms
    assert P_SUPERVISOR_ACTION in adm_perms


def test_service_entrypoints_importable() -> None:
    from app.modules.supervisor_lm.service import (
        ManagerApprovalType,
        SupervisorActionType,
        SupervisorActor,
        create_failed_delivery_review,
        create_manager_approval,
        create_order_escalation,
        create_risk_flag,
        record_manager_decision,
        record_supervisor_action,
    )

    assert SupervisorActionType.FLAG_RISK.value == "FLAG_RISK"
    assert ManagerApprovalType.RIDER_REASSIGNMENT.value == "RIDER_REASSIGNMENT"
    actor = SupervisorActor(actor_id="u-1", role="supervisor")
    assert actor.actor_id == "u-1"
    # Just verify they are callables (signature check happens via FastAPI at boot)
    for fn in (
        record_supervisor_action,
        record_manager_decision,
        create_manager_approval,
        create_order_escalation,
        create_risk_flag,
        create_failed_delivery_review,
    ):
        assert callable(fn)


def test_app_mounts_supervisor_routes() -> None:
    """End-to-end: full app boot includes 10 /supervisor/ paths."""
    from app.main import create_app

    app = create_app()
    paths = [r.path for r in app.routes if hasattr(r, "path")]
    sup = [p for p in paths if p.startswith("/api/v1/admin/supervisor/")]
    assert len(sup) == 10, f"expected 10 supervisor routes, got {len(sup)}: {sup}"
