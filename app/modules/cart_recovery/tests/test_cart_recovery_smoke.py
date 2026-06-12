"""Smoke test — imports + render the templates with a sample ctx."""
from __future__ import annotations


def test_imports_and_render():
    from app.modules.cart_recovery import (
        codes,
        dispatch,
        jobs,
        models,
        repository,
        service,
        templates,
    )
    # Touch each module so the import side-effects run.
    assert codes.MILESTONE_1H == "cart_1h"
    assert models.HypershopCartRecoveryDispatch.__tablename__ == (
        "hypershop_cart_recovery_dispatches"
    )
    assert hasattr(repository, "list_carts_due_for_milestone")
    assert hasattr(service, "dispatch_for_cart")
    assert hasattr(jobs, "scan_abandoned_carts_job")
    assert hasattr(dispatch, "send_whatsapp")

    r = templates.render(
        "cart_1h", "whatsapp", "bn",
        {"customer_name": "রহিম", "item_count": 3, "cart_url": "https://x"},
    )
    assert "রহিম" in r["body"]
    assert "3" in r["body"]
    assert "https://x" in r["body"]
    assert r["template_code"] == "cart_1h_whatsapp_bn"

    r2 = templates.render(
        "winback_30d", "email", "en",
        {"customer_name": "Alice", "home_url": "https://h"},
    )
    assert "Alice" in r2["body"]
    assert r2["subject"] is not None
