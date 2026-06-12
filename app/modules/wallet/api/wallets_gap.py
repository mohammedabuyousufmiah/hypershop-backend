"""Admin wallet read endpoints (gap-fill for the admin Growth panel).

Self-contained router that powers the admin-panel ``AdminGrowthClient``
Wallets tab. The customer-facing router (``api/customer.py``) only exposes
``/wallets/me*``; the admin list + detail + ledger reads were missing, so the
FE's ``api.walletsAdmin.{list,get,listTransactions}`` calls 404'd.

Wire shapes returned here MUST match ``@ecom/types``:

  GET /wallets                         -> WalletPageWire          {items, next_cursor}
  GET /wallets/{id}                    -> WalletWire
  GET /wallets/{id}/transactions       -> WalletTransactionPageWire {items, next_cursor}

The response-envelope middleware wraps each return value as ``{success, data}``;
we therefore return the *raw* dict/list the FE reads as ``.data``.

Defensive by construction: every query is raw ``text()`` SQL wrapped in
try/except. A missing table / column / DB hiccup degrades to an empty page of
the correct shape rather than a 500. The path param is typed ``UUID`` so the
literal ``/wallets/me`` route on the customer router still resolves first.

Read perm is ``dashboard.read`` (the perm every admin/staff principal holds and
the same gate used by the sibling fraud / analytics admin readers).
"""
from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission

router = APIRouter(prefix="/wallets", tags=["admin-wallets"])

_READ = "dashboard.read"

# kind ('credit' | 'debit' | 'adjust') -> FE WalletTxnDirection / WalletTxnType.
_DIRECTION_BY_KIND = {"credit": "CREDIT", "debit": "DEBIT", "adjust": "CREDIT"}
_TXN_TYPE_BY_KIND = {
    "credit": "ADMIN_CREDIT",
    "debit": "ADMIN_DEBIT",
    "adjust": "ADJUSTMENT",
}


def _minor_to_decimal_str(minor: int | None, places: int = 2) -> str:
    """1234 -> '12.34'. Float-free for money safety. None -> '0.00'."""
    m = int(minor or 0)
    sign = "-" if m < 0 else ""
    m = abs(m)
    base = 10 ** places
    whole, frac = divmod(m, base)
    return f"{sign}{whole}.{frac:0{places}d}"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _wallet_wire(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "customer_id": str(row.customer_user_id),
        "currency": row.currency or "BDT",
        "balance": _minor_to_decimal_str(row.balance_minor),
        "status": row.status or "ACTIVE",
        "last_activity_at": _iso(row.last_activity_at),
        "created_at": _iso(row.created_at),
    }


def _txn_wire(row: Any, currency: str) -> dict[str, Any]:
    kind = (row.kind or "").lower()
    return {
        "id": str(row.id),
        "wallet_id": str(row.wallet_id),
        "direction": _DIRECTION_BY_KIND.get(kind, "CREDIT"),
        "txn_type": _TXN_TYPE_BY_KIND.get(kind, "ADJUSTMENT"),
        "amount": _minor_to_decimal_str(row.amount_minor),
        "currency": currency or "BDT",
        "balance_after": _minor_to_decimal_str(row.balance_after_minor),
        "reference_type": getattr(row, "source_type", None),
        "reference_id": (
            str(row.source_id) if getattr(row, "source_id", None) else None
        ),
        "idempotency_key": None,
        "reason": getattr(row, "memo", None),
        "actor_id": None,
        "created_at": _iso(row.created_at),
    }


@router.get(
    "",
    dependencies=[Depends(requires_permission(_READ))],
    summary="List wallets (admin) — WalletPageWire",
)
async def admin_list_wallets(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status: str | None = Query(default=None),
    customer_id: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    try:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            # FE may send a comma-joined multi-status string.
            statuses = [s.strip() for s in str(status).split(",") if s.strip()]
            if statuses:
                clauses.append("status = ANY(:statuses)")
                params["statuses"] = statuses
        if customer_id:
            clauses.append("customer_user_id = :customer_id")
            params["customer_id"] = customer_id
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = text(
            "SELECT id, customer_user_id, currency, balance_minor, status, "
            "last_activity_at, created_at "
            "FROM hypershop_wallets"
            f"{where} "
            "ORDER BY created_at DESC "
            "LIMIT :limit OFFSET :offset"
        )
        async with uow.transactional() as session:
            result = await session.execute(sql, params)
            rows = result.fetchall()
        items = [_wallet_wire(r) for r in rows]
    except Exception:
        items = []
    next_cursor = str(offset + limit) if len(items) == limit else None
    return {"items": items, "next_cursor": next_cursor}


@router.get(
    "/{wallet_id}",
    dependencies=[Depends(requires_permission(_READ))],
    summary="Get one wallet (admin) — WalletWire",
)
async def admin_get_wallet(
    wallet_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any] | None:
    try:
        sql = text(
            "SELECT id, customer_user_id, currency, balance_minor, status, "
            "last_activity_at, created_at "
            "FROM hypershop_wallets WHERE id = :wid LIMIT 1"
        )
        async with uow.transactional() as session:
            result = await session.execute(sql, {"wid": str(wallet_id)})
            row = result.fetchone()
        if row is None:
            return None
        return _wallet_wire(row)
    except Exception:
        return None


@router.get(
    "/{wallet_id}/transactions",
    dependencies=[Depends(requires_permission(_READ))],
    summary="List wallet transactions (admin) — WalletTransactionPageWire",
)
async def admin_list_wallet_transactions(
    wallet_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    direction: str | None = Query(default=None),
    txn_type: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    try:
        currency = "BDT"
        params: dict[str, Any] = {
            "wid": str(wallet_id),
            "limit": limit,
            "offset": offset,
        }
        clauses = ["wallet_id = :wid"]
        # Map FE direction filter back to stored ``kind`` values.
        if direction:
            d = str(direction).strip().upper()
            if d == "DEBIT":
                clauses.append("kind = 'debit'")
            elif d == "CREDIT":
                clauses.append("kind IN ('credit', 'adjust')")
        where = " AND ".join(clauses)
        sql = text(
            "SELECT id, wallet_id, kind, amount_minor, balance_after_minor, "
            "source_type, source_id, memo, created_at "
            "FROM hypershop_wallet_txns "
            f"WHERE {where} "
            "ORDER BY created_at DESC "
            "LIMIT :limit OFFSET :offset"
        )
        async with uow.transactional() as session:
            cur_res = await session.execute(
                text(
                    "SELECT currency FROM hypershop_wallets "
                    "WHERE id = :wid LIMIT 1"
                ),
                {"wid": str(wallet_id)},
            )
            cur_row = cur_res.fetchone()
            if cur_row is not None and cur_row.currency:
                currency = cur_row.currency
            result = await session.execute(sql, params)
            rows = result.fetchall()
        items = [_txn_wire(r, currency) for r in rows]
    except Exception:
        items = []
    next_cursor = str(offset + limit) if len(items) == limit else None
    return {"items": items, "next_cursor": next_cursor}
