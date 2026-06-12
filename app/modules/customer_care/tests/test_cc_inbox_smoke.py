"""Smoke test — assert the 4 new cc_inbox tables register on Base.metadata."""
from __future__ import annotations


def test_cc_inbox_tables_register() -> None:
    from app.core.db.base import Base
    # Side-effect imports
    from app.modules.customer_care import cc_inbox_models  # noqa: F401
    from app.modules.customer_care import cc_inbox_repository  # noqa: F401
    from app.modules.customer_care import cc_inbox_service  # noqa: F401
    from app.modules.customer_care import cc_inbox_jobs  # noqa: F401
    from app.modules.customer_care.api import inbox as _inbox_api  # noqa: F401
    from app.modules.customer_care.api import voice_calls_admin as _vc_api  # noqa: F401
    from app.modules.customer_care.api import csat_admin as _csat_api  # noqa: F401

    expected = {
        "hypershop_cc_threads",
        "hypershop_cc_messages",
        "hypershop_voice_call_sessions",
        "hypershop_cc_csat_surveys",
    }
    names = set(Base.metadata.tables.keys())
    missing = expected - names
    assert not missing, f"Missing tables in metadata: {missing}"


def test_cc_inbox_router_endpoints_exist() -> None:
    from app.modules.customer_care.api.inbox import router as inbox_router
    from app.modules.customer_care.api.voice_calls_admin import (
        router as vc_router,
    )
    from app.modules.customer_care.api.csat_admin import router as csat_router

    inbox_paths = {r.path for r in inbox_router.routes}
    vc_paths = {r.path for r in vc_router.routes}
    csat_paths = {r.path for r in csat_router.routes}

    assert "/admin/cc/inbox/threads" in inbox_paths
    assert "/admin/cc/inbox/threads/{thread_id}/messages" in inbox_paths
    assert "/admin/cc/inbox/threads/{thread_id}/ai-draft" in inbox_paths
    assert "/admin/cc/voice-calls" in vc_paths
    assert "/admin/cc/voice-calls/{call_id}/assign" in vc_paths
    assert "/admin/cc/csat" in csat_paths
    assert "/admin/cc/csat/summary" in csat_paths


def test_cc_inbox_permissions_in_catalog() -> None:
    from app.modules.iam.permissions import ALL_PERMISSIONS
    for perm in (
        "cc_inbox.view", "cc_inbox.reply", "cc_inbox.admin", "voice_call.view",
    ):
        assert perm in ALL_PERMISSIONS, f"perm {perm!r} missing from ALL_PERMISSIONS"


def test_admin_module_registry_has_cc_inbox() -> None:
    from app.core.registry.admin_modules import ADMIN_MODULES
    codes = {m.code for m in ADMIN_MODULES}
    assert "cc_inbox" in codes
