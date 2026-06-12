"""Admin *settlements* gap router.

Self-contained, boot-safe GET surface backing the admin-panel
``AdminSettlementsClient`` (Settlements / Ledger / Payout-batches tabs).

The frontend wire contracts (see ``@ecom/types``) are:

* ``SettlementListWire``   -> {total, page, page_size, items: SettlementSummaryWire[]}
* ``SettlementDetailWire``  -> full settlement + ``lines: SettlementLineWire[]``
* ``LedgerListWire``        -> {total, page, page_size, items: LedgerEntryWire[]}
* ``PayoutBatchListWire``   -> {total, page, page_size, items: PayoutBatchSummaryWire[]}
* ``PayoutBatchDetailWire`` -> full batch + ``items: PayoutBatchItemWire[]``

There is currently **no** dedicated marketplace-settlement / seller-ledger /
generic payout-batch table in this build (finance ships ``fin_*`` +
``finance_seller_payout_batches`` for COD, but the column shape does not match
the FE settlement contract). To keep the FE wired (empty state, not 404) every
query is wrapped in ``try/except`` and degrades to a correctly-shaped empty
page. If/when the backing tables land, the SQL here can be tightened to project
real rows without touching the FE.

This module is registered centrally in ``app/main.py`` — it does not edit
any existing router.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission

router = APIRouter(prefix="", tags=["admin-settlements"])

# Admins hold ``finance.read`` (same gate the finance dashboard uses). Reusing
# it keeps this read-only surface consistent with the rest of the module.
_READ = "finance.read"


def _empty_page(page: int, page_size: int) -> dict[str, Any]:
    """Correctly-shaped empty paged wrapper the FE list views expect."""
    return {"total": 0, "page": page, "page_size": page_size, "items": []}


# --------------------------------------------------------------------------- #
# Settlements
# --------------------------------------------------------------------------- #


@router.get(
    "/settlements/admin",
    summary="Admin: list seller settlements (paged)",
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_settlements(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status: Annotated[str | None, Query()] = None,
    seller_id: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Return a ``SettlementListWire`` page.

    No marketplace-settlement table exists in this build, so this returns an
    empty page (the FE renders its empty state instead of erroring). The query
    is defensive so a future ``seller_settlements`` table is a drop-in.
    """
    try:
        async with uow.transactional():
            # Intentionally no real projection yet — shape-only contract.
            return _empty_page(page, page_size)
    except Exception:  # noqa: BLE001 - never 500 the FE
        return _empty_page(page, page_size)


@router.get(
    "/settlements/admin/ledger",
    summary="Admin: browse the seller ledger (paged)",
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_ledger(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    seller_id: Annotated[str | None, Query()] = None,
    kind: Annotated[str | None, Query()] = None,
    settled: Annotated[bool | None, Query()] = None,
    period_start: Annotated[str | None, Query()] = None,
    period_end: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    """Return a ``LedgerListWire`` page.

    No seller-ledger table exists in this build; returns an empty page so the
    Ledger tab connects and renders its empty state.
    """
    try:
        async with uow.transactional():
            return _empty_page(page, page_size)
    except Exception:  # noqa: BLE001
        return _empty_page(page, page_size)


@router.get(
    "/settlements/admin/{settlement_id}",
    summary="Admin: settlement detail",
    dependencies=[Depends(requires_permission(_READ))],
)
async def get_settlement(
    settlement_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    """Return a ``SettlementDetailWire``.

    With no backing table, return a shape-complete detail object (all fields
    present, empty ``lines``) so the detail dialog renders rather than 404s.
    """
    try:
        sid = str(UUID(settlement_id))
    except (ValueError, TypeError):
        sid = settlement_id

    try:
        async with uow.transactional():
            return {
                "id": sid,
                "seller_id": "",
                "period_start": "",
                "period_end": "",
                "currency": "BDT",
                "status": "OPEN",
                "gross_credits": "0",
                "gross_debits": "0",
                "commission_total": "0",
                "net_payable": "0",
                "entries_count": 0,
                "closed_at": None,
                "closed_by": None,
                "paid_at": None,
                "paid_by": None,
                "voided_at": None,
                "voided_by": None,
                "payout_reference": None,
                "notes": None,
                "created_at": "",
                "lines": [],
            }
    except Exception:  # noqa: BLE001
        return {
            "id": sid,
            "seller_id": "",
            "period_start": "",
            "period_end": "",
            "currency": "BDT",
            "status": "OPEN",
            "gross_credits": "0",
            "gross_debits": "0",
            "commission_total": "0",
            "net_payable": "0",
            "entries_count": 0,
            "closed_at": None,
            "closed_by": None,
            "paid_at": None,
            "paid_by": None,
            "voided_at": None,
            "voided_by": None,
            "payout_reference": None,
            "notes": None,
            "created_at": "",
            "lines": [],
        }


# --------------------------------------------------------------------------- #
# Payout batches
# --------------------------------------------------------------------------- #


@router.get(
    "/payout-batches",
    summary="Admin: list payout batches (paged)",
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_payout_batches(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status: Annotated[str | None, Query()] = None,
    currency: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Return a ``PayoutBatchListWire`` page.

    The generic marketplace payout-batch table backing this FE contract does
    not exist in this build (the existing ``finance_seller_payout_batches`` is
    a COD-settlement shape with different columns), so this returns an empty
    page. The FE renders its empty state instead of a 404.
    """
    try:
        async with uow.transactional():
            return _empty_page(page, page_size)
    except Exception:  # noqa: BLE001
        return _empty_page(page, page_size)


@router.get(
    "/payout-batches/{batch_id}",
    summary="Admin: payout batch detail",
    dependencies=[Depends(requires_permission(_READ))],
)
async def get_payout_batch(
    batch_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    """Return a ``PayoutBatchDetailWire``.

    Shape-complete detail object (empty ``items``) so the detail dialog
    renders instead of 404ing when the backing table is absent.
    """
    try:
        bid = str(UUID(batch_id))
    except (ValueError, TypeError):
        bid = batch_id

    detail = {
        "id": bid,
        "code": "",
        "status": "DRAFT",
        "currency": "BDT",
        "total_amount": "0",
        "item_count": 0,
        "target_provider": "",
        "memo": None,
        "created_by": None,
        "approved_by": None,
        "approved_at": None,
        "executed_by": None,
        "executed_at": None,
        "voided_by": None,
        "voided_at": None,
        "void_reason": None,
        "created_at": "",
        "items": [],
    }
    try:
        async with uow.transactional():
            return detail
    except Exception:  # noqa: BLE001
        return detail
