"""Smoke test: module imports + ORM registry sees the 4 dispute tables."""
from __future__ import annotations


def test_module_imports_clean() -> None:
    from app.modules.disputes import (
        codes,
        jobs,
        models,
        repository,
        schemas,
        service,
    )
    from app.modules.disputes.api import admin_router, buyer_router, seller_router

    assert codes.STATUS_OPEN == "open"
    assert hasattr(models, "HypershopDispute")
    assert hasattr(models, "HypershopDisputeMessage")
    assert hasattr(models, "HypershopDisputeEvidence")
    assert hasattr(models, "HypershopEscrowHold")
    assert hasattr(repository, "create_dispute")
    assert hasattr(service, "open_dispute")
    assert hasattr(service, "mediator_decide")
    assert hasattr(jobs, "auto_escalate_overdue_disputes_job")
    assert buyer_router.prefix == "/disputes"
    assert seller_router.prefix == "/seller/disputes"
    assert admin_router.prefix == "/admin/disputes"
    for s in schemas.DisputeListResponse.model_fields:
        assert s


def test_registry_sees_dispute_models() -> None:
    from app.core.db.base import Base
    import app.modules.disputes.models  # noqa: F401

    tables = set(Base.metadata.tables.keys())
    expected = {
        "hypershop_disputes",
        "hypershop_dispute_messages",
        "hypershop_dispute_evidence",
        "hypershop_escrow_holds",
    }
    assert expected.issubset(tables), f"missing: {expected - tables}"
