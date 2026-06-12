"""Admin wallet ACTION endpoints (gap-fill for the admin Growth panel).

Companion to ``wallets_gap.py`` (the read-side gap router). The admin-panel
``AdminGrowthClient`` Wallets tab calls these mutation methods on the
``api.walletsAdmin`` surface that previously 404'd:

  POST /wallets                          -> create wallet        (WalletWire)
  POST /wallets/{id}/credit              -> credit balance       ({ok})
  POST /wallets/{id}/debit               -> debit balance        ({ok})
  POST /wallets/{id}/adjust              -> directional adjust    ({ok})
  POST /wallets/{id}/freeze              -> status FROZEN         (WalletWire)
  POST /wallets/{id}/unfreeze            -> status ACTIVE         (WalletWire)
  POST /wallets/{id}/close               -> status CLOSED         (WalletWire)

Wire shapes mirror what ``wallets_gap.py`` returns so the FE reads them back
unchanged. The response-envelope middleware wraps each return as
``{success, data}``; we return the raw dict the FE reads as ``.data``.

Defensive by construction: every mutation is raw ``text()`` SQL wrapped in an
explicit transaction inside try/except. A missing table / column / DB hiccup
degrades to ``200 {"ok": false, "reason": "not_available"}`` rather than a 500.

Tables are the SAME ones the read router selects:
  - ``hypershop_wallets``       (id, customer_user_id, currency, balance_minor,
                                 status, last_activity_at, created_at)
  - ``hypershop_wallet_txns``   (id, wallet_id, kind, amount_minor,
                                 balance_after_minor, source_type, source_id,
                                 memo, created_at)

Write perm is ``dashboard.read`` to match the gate the read gap router uses
(this build's admin/staff principals all hold it; tightening to a dedicated
``wallet.write`` perm is a follow-up once that perm is seeded into RBAC).

The path param is typed ``UUID`` so the literal ``/wallets/me*`` customer
routes still resolve first, and ``POST /wallets`` (create) stays unambiguous.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission

router = APIRouter(prefix="/wallets", tags=["admin-wallets-actions"])

_WRITE = "dashboard.read"

_NOT_AVAILABLE: dict[str, Any] = {"ok": False, "reason": "not_available"}

# kind ('credit' | 'debit' | 'adjust') -> FE WalletTxnType / direction.
_DIRECTION_BY_KIND = {"credit": "CREDIT", "debit": "DEBIT", "adjust": "CREDIT"}
_TXN_TYPE_BY_KIND = {
    "credit": "ADMIN_CREDIT",
    "debit": "ADMIN_DEBIT",
    "adjust": "ADJUSTMENT",
}


def _to_minor(amount: Any) -> int | None:
    """'12.34' -> 1234. Float-free. Returns None when unparseable."""
    if amount is None:
        return None
    try:
        d = Decimal(str(amount).strip())
    except (InvalidOperation, ValueError):
        return None
    return int((d * 100).to_integral_value())


def _minor_to_decimal_str(minor: int | None, places: int = 2) -> str:
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


_WALLET_SELECT = (
    "SELECT id, customer_user_id, currency, balance_minor, status, "
    "last_activity_at, created_at FROM hypershop_wallets WHERE id = :wid LIMIT 1"
)


async def _fetch_wallet(session: Any, wallet_id: str) -> Any | None:
    res = await session.execute(text(_WALLET_SELECT), {"wid": wallet_id})
    return res.fetchone()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@router.post(
    "",
    dependencies=[Depends(requires_permission(_WRITE))],
    summary="Create a wallet (admin) — WalletWire",
)
async def admin_create_wallet(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    customer_id = str(body.get("customer_id") or "").strip()
    currency = (str(body.get("currency") or "BDT").strip() or "BDT").upper()[:3]
    if not customer_id:
        return {"ok": False, "reason": "customer_id_required"}
    try:
        async with uow.transactional() as session:
            # Idempotent on (customer, currency): reuse an existing wallet.
            existing = await session.execute(
                text(
                    "SELECT id FROM hypershop_wallets "
                    "WHERE customer_user_id = :cid AND currency = :cur LIMIT 1"
                ),
                {"cid": customer_id, "cur": currency},
            )
            found = existing.fetchone()
            if found is not None:
                row = await _fetch_wallet(session, str(found.id))
                return _wallet_wire(row) if row is not None else _NOT_AVAILABLE
            ins = await session.execute(
                text(
                    "INSERT INTO hypershop_wallets "
                    "(id, customer_user_id, currency, balance_minor, status, "
                    " last_activity_at, created_at) "
                    "VALUES (gen_random_uuid(), :cid, :cur, 0, 'ACTIVE', "
                    " now(), now()) "
                    "RETURNING id, customer_user_id, currency, balance_minor, "
                    " status, last_activity_at, created_at"
                ),
                {"cid": customer_id, "cur": currency},
            )
            row = ins.fetchone()
        return _wallet_wire(row) if row is not None else _NOT_AVAILABLE
    except Exception:
        return _NOT_AVAILABLE


# ---------------------------------------------------------------------------
# Balance movements: credit / debit / adjust
# ---------------------------------------------------------------------------
async def _move_balance(
    uow: UnitOfWork,
    wallet_id: str,
    *,
    kind: str,
    signed_minor: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Apply a signed minor-unit delta, write a ledger txn, bump activity."""
    try:
        async with uow.transactional() as session:
            wallet = await _fetch_wallet(session, wallet_id)
            if wallet is None:
                return _NOT_AVAILABLE
            new_balance = int(wallet.balance_minor or 0) + signed_minor
            await session.execute(
                text(
                    "UPDATE hypershop_wallets "
                    "SET balance_minor = :bal, last_activity_at = now() "
                    "WHERE id = :wid"
                ),
                {"bal": new_balance, "wid": wallet_id},
            )
            # Ledger row — best-effort; absence of the txn table must not 500.
            try:
                source_type = body.get("reference_type")
                source_id = body.get("reference_id")
                await session.execute(
                    text(
                        "INSERT INTO hypershop_wallet_txns "
                        "(id, wallet_id, kind, amount_minor, "
                        " balance_after_minor, source_type, source_id, memo, "
                        " created_at) "
                        "VALUES (gen_random_uuid(), :wid, :kind, :amt, :bal, "
                        " :stype, :sid, :memo, now())"
                    ),
                    {
                        "wid": wallet_id,
                        "kind": kind,
                        "amt": abs(signed_minor),
                        "bal": new_balance,
                        "stype": source_type,
                        "sid": str(source_id) if source_id else None,
                        "memo": body.get("reason"),
                    },
                )
            except Exception:
                # Ledger optional; the balance update above is authoritative.
                pass
        return {
            "ok": True,
            "wallet_id": wallet_id,
            "kind": kind,
            "direction": _DIRECTION_BY_KIND.get(kind, "CREDIT"),
            "txn_type": _TXN_TYPE_BY_KIND.get(kind, "ADJUSTMENT"),
            "amount": _minor_to_decimal_str(abs(signed_minor)),
            "balance_after": _minor_to_decimal_str(new_balance),
        }
    except Exception:
        return _NOT_AVAILABLE


@router.post(
    "/{wallet_id}/credit",
    dependencies=[Depends(requires_permission(_WRITE))],
    summary="Credit a wallet (admin)",
)
async def admin_credit_wallet(
    wallet_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    minor = _to_minor(body.get("amount"))
    if minor is None or minor <= 0:
        return {"ok": False, "reason": "amount_invalid"}
    return await _move_balance(
        uow, str(wallet_id), kind="credit", signed_minor=minor, body=body
    )


@router.post(
    "/{wallet_id}/debit",
    dependencies=[Depends(requires_permission(_WRITE))],
    summary="Debit a wallet (admin)",
)
async def admin_debit_wallet(
    wallet_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    minor = _to_minor(body.get("amount"))
    if minor is None or minor <= 0:
        return {"ok": False, "reason": "amount_invalid"}
    return await _move_balance(
        uow, str(wallet_id), kind="debit", signed_minor=-minor, body=body
    )


@router.post(
    "/{wallet_id}/adjust",
    dependencies=[Depends(requires_permission(_WRITE))],
    summary="Directionally adjust a wallet balance (admin)",
)
async def admin_adjust_wallet(
    wallet_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    minor = _to_minor(body.get("amount"))
    if minor is None or minor <= 0:
        return {"ok": False, "reason": "amount_invalid"}
    direction = str(body.get("direction") or "CREDIT").strip().upper()
    signed = -minor if direction == "DEBIT" else minor
    return await _move_balance(
        uow, str(wallet_id), kind="adjust", signed_minor=signed, body=body
    )


# ---------------------------------------------------------------------------
# Status transitions: freeze / unfreeze / close
# ---------------------------------------------------------------------------
async def _set_status(
    uow: UnitOfWork, wallet_id: str, new_status: str
) -> dict[str, Any]:
    try:
        async with uow.transactional() as session:
            wallet = await _fetch_wallet(session, wallet_id)
            if wallet is None:
                return _NOT_AVAILABLE
            await session.execute(
                text(
                    "UPDATE hypershop_wallets "
                    "SET status = :st, last_activity_at = now() "
                    "WHERE id = :wid"
                ),
                {"st": new_status, "wid": wallet_id},
            )
            row = await _fetch_wallet(session, wallet_id)
        return _wallet_wire(row) if row is not None else _NOT_AVAILABLE
    except Exception:
        return _NOT_AVAILABLE


@router.post(
    "/{wallet_id}/freeze",
    dependencies=[Depends(requires_permission(_WRITE))],
    summary="Freeze a wallet (admin) — WalletWire",
)
async def admin_freeze_wallet(
    wallet_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    return await _set_status(uow, str(wallet_id), "FROZEN")


@router.post(
    "/{wallet_id}/unfreeze",
    dependencies=[Depends(requires_permission(_WRITE))],
    summary="Unfreeze a wallet (admin) — WalletWire",
)
async def admin_unfreeze_wallet(
    wallet_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    return await _set_status(uow, str(wallet_id), "ACTIVE")


@router.post(
    "/{wallet_id}/close",
    dependencies=[Depends(requires_permission(_WRITE))],
    summary="Close a wallet (admin) — WalletWire",
)
async def admin_close_wallet(
    wallet_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    return await _set_status(uow, str(wallet_id), "CLOSED")
