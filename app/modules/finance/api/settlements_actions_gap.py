"""Admin *settlements* ACTION router (mutation gap surface).

Self-contained, boot-safe POST surface backing the mutating verbs the
admin-panel ``AdminSettlementsClient`` performs on the Settlements / Ledger /
Payout-batches tabs:

* ``POST /settlements/open``                 — open a new OPEN settlement period
* ``POST /settlements/close-period``         — close by (seller, period, ccy)
* ``POST /settlements/ledger/adjustments``   — append a signed ADJUSTMENT entry
* ``POST /settlements/{id}/close``           — OPEN  -> CLOSED
* ``POST /settlements/{id}/pay``             — CLOSED -> PAID
* ``POST /settlements/{id}/void``            — OPEN/CLOSED -> VOIDED
* ``POST /payout-batches``                   — create a DRAFT batch
* ``POST /payout-batches/{id}/approve``      — DRAFT -> APPROVED (maker-checker)
* ``POST /payout-batches/{id}/execute``      — APPROVED -> EXECUTED
* ``POST /payout-batches/{id}/void``         — DRAFT/APPROVED -> VOIDED

There is currently **no** dedicated marketplace-settlement / seller-ledger /
generic payout-batch table in this build whose columns match the FE contract
(finance ships ``fin_*`` for journal/AP/COD, which is a different shape). To
make this feature genuinely operational *without* touching the Alembic chain,
every action lazily ensures a small dedicated backing table exists via
``CREATE TABLE IF NOT EXISTS`` (idempotent, boot-safe, no model import) and
then performs a real ``INSERT`` / ``UPDATE`` against it inside the request's
unit-of-work transaction. The write therefore persists real rows and the
endpoint returns the real row it just wrote.

Defensive contract: every handler is wrapped so that if the table/columns are
unavailable (e.g. the DB role can't ``CREATE TABLE``), it returns a clean
``200 {"ok": false, "reason": "not_available"}`` — it never 500s the FE. An
optional ``Idempotency-Key`` header is accepted and ignored as a no-op (the
INSERTs are id-generated; re-runs simply create distinct rows, and the
state-transition UPDATEs are naturally idempotent on terminal states).

This module is registered centrally in ``app/main.py`` — it does not edit any
existing router, the Alembic chain, or ``main.py`` itself.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission

# Same prefix as the GET gap router (``prefix=""`` mounted at ``api_prefix``).
router = APIRouter(prefix="", tags=["admin-settlements-actions"])

# Same gate the GET gap surface uses. ``requires_permission`` returns the
# resolved Principal, so we take it as a parameter to stamp the actor onto
# audit columns (created_by / approved_by / etc.).
_READ = "finance.read"

# Dedicated, self-owned backing tables (do not collide with the ``fin_*`` or
# ``finance_seller_payout_batches`` schema owned by the finance migrations).
_SETTLE_TBL = "settlements_gap_settlements"
_LEDGER_TBL = "settlements_gap_ledger"
_BATCH_TBL = "settlements_gap_payout_batches"
_BATCH_ITEM_TBL = "settlements_gap_payout_batch_items"

_NOT_AVAILABLE: dict[str, Any] = {"ok": False, "reason": "not_available"}


# --------------------------------------------------------------------------- #
# Lazy schema bootstrap (idempotent, boot-safe)
# --------------------------------------------------------------------------- #


_DDL_SETTLEMENTS = f"""
CREATE TABLE IF NOT EXISTS {_SETTLE_TBL} (
    id               UUID PRIMARY KEY,
    seller_id        TEXT NOT NULL,
    period_start     TEXT NOT NULL,
    period_end       TEXT NOT NULL,
    currency         TEXT NOT NULL DEFAULT 'BDT',
    status           TEXT NOT NULL DEFAULT 'OPEN',
    gross_credits    NUMERIC(20, 4) NOT NULL DEFAULT 0,
    gross_debits     NUMERIC(20, 4) NOT NULL DEFAULT 0,
    commission_total NUMERIC(20, 4) NOT NULL DEFAULT 0,
    net_payable      NUMERIC(20, 4) NOT NULL DEFAULT 0,
    entries_count    INTEGER NOT NULL DEFAULT 0,
    closed_at        TIMESTAMPTZ,
    closed_by        TEXT,
    paid_at          TIMESTAMPTZ,
    paid_by          TEXT,
    voided_at        TIMESTAMPTZ,
    voided_by        TEXT,
    payout_reference TEXT,
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_DDL_LEDGER = f"""
CREATE TABLE IF NOT EXISTS {_LEDGER_TBL} (
    id                UUID PRIMARY KEY,
    seller_id         TEXT NOT NULL,
    settlement_id     UUID,
    kind              TEXT NOT NULL DEFAULT 'ADJUSTMENT',
    amount            NUMERIC(20, 4) NOT NULL DEFAULT 0,
    commission_amount NUMERIC(20, 4) NOT NULL DEFAULT 0,
    seller_net        NUMERIC(20, 4) NOT NULL DEFAULT 0,
    currency          TEXT NOT NULL DEFAULT 'BDT',
    settled           BOOLEAN NOT NULL DEFAULT FALSE,
    occurred_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    memo              TEXT,
    order_id          TEXT,
    return_id         TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_DDL_BATCH = f"""
CREATE TABLE IF NOT EXISTS {_BATCH_TBL} (
    id              UUID PRIMARY KEY,
    code            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'DRAFT',
    currency        TEXT NOT NULL DEFAULT 'BDT',
    total_amount    NUMERIC(20, 4) NOT NULL DEFAULT 0,
    item_count      INTEGER NOT NULL DEFAULT 0,
    target_provider TEXT NOT NULL DEFAULT '',
    memo            TEXT,
    created_by      TEXT,
    approved_by     TEXT,
    approved_at     TIMESTAMPTZ,
    executed_by     TEXT,
    executed_at     TIMESTAMPTZ,
    voided_by       TEXT,
    voided_at       TIMESTAMPTZ,
    void_reason     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_DDL_BATCH_ITEM = f"""
CREATE TABLE IF NOT EXISTS {_BATCH_ITEM_TBL} (
    id             UUID PRIMARY KEY,
    batch_id       UUID NOT NULL,
    settlement_id  UUID,
    seller_id      TEXT NOT NULL DEFAULT '',
    amount         NUMERIC(20, 4) NOT NULL DEFAULT 0,
    currency       TEXT NOT NULL DEFAULT 'BDT',
    status         TEXT NOT NULL DEFAULT 'PENDING',
    provider_ref   TEXT,
    failure_reason TEXT,
    executed_at    TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


async def _ensure_schema(session: AsyncSession) -> None:
    """Create the dedicated backing tables if absent. Idempotent."""
    await session.execute(text(_DDL_SETTLEMENTS))
    await session.execute(text(_DDL_LEDGER))
    await session.execute(text(_DDL_BATCH))
    await session.execute(text(_DDL_BATCH_ITEM))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _actor(principal: Principal) -> str:
    try:
        return str(principal.user_id)
    except Exception:  # noqa: BLE001
        return "system"


def _coerce_uuid(raw: str) -> str:
    try:
        return str(UUID(raw))
    except (ValueError, TypeError):
        return raw


def _settlement_row(m: Any) -> dict[str, Any]:
    """Project a settlements row mapping to the ``SettlementDetailWire`` shape."""
    return {
        "id": str(m["id"]),
        "seller_id": m["seller_id"] or "",
        "period_start": m["period_start"] or "",
        "period_end": m["period_end"] or "",
        "currency": m["currency"] or "BDT",
        "status": m["status"] or "OPEN",
        "gross_credits": str(m["gross_credits"]),
        "gross_debits": str(m["gross_debits"]),
        "commission_total": str(m["commission_total"]),
        "net_payable": str(m["net_payable"]),
        "entries_count": int(m["entries_count"] or 0),
        "closed_at": m["closed_at"].isoformat() if m["closed_at"] else None,
        "closed_by": m["closed_by"],
        "paid_at": m["paid_at"].isoformat() if m["paid_at"] else None,
        "paid_by": m["paid_by"],
        "voided_at": m["voided_at"].isoformat() if m["voided_at"] else None,
        "voided_by": m["voided_by"],
        "payout_reference": m["payout_reference"],
        "notes": m["notes"],
        "created_at": m["created_at"].isoformat() if m["created_at"] else "",
        "lines": [],
    }


def _ledger_row(m: Any) -> dict[str, Any]:
    return {
        "id": str(m["id"]),
        "seller_id": m["seller_id"] or "",
        "settlement_id": str(m["settlement_id"]) if m["settlement_id"] else None,
        "kind": m["kind"] or "ADJUSTMENT",
        "amount": str(m["amount"]),
        "commission_amount": str(m["commission_amount"]),
        "seller_net": str(m["seller_net"]),
        "currency": m["currency"] or "BDT",
        "settled": bool(m["settled"]),
        "occurred_at": m["occurred_at"].isoformat() if m["occurred_at"] else "",
        "memo": m["memo"],
        "created_at": m["created_at"].isoformat() if m["created_at"] else "",
    }


def _batch_row(m: Any) -> dict[str, Any]:
    return {
        "id": str(m["id"]),
        "code": m["code"] or "",
        "status": m["status"] or "DRAFT",
        "currency": m["currency"] or "BDT",
        "total_amount": str(m["total_amount"]),
        "item_count": int(m["item_count"] or 0),
        "target_provider": m["target_provider"] or "",
        "memo": m["memo"],
        "created_by": m["created_by"],
        "approved_by": m["approved_by"],
        "approved_at": m["approved_at"].isoformat() if m["approved_at"] else None,
        "executed_by": m["executed_by"],
        "executed_at": m["executed_at"].isoformat() if m["executed_at"] else None,
        "voided_by": m["voided_by"],
        "voided_at": m["voided_at"].isoformat() if m["voided_at"] else None,
        "void_reason": m["void_reason"],
        "created_at": m["created_at"].isoformat() if m["created_at"] else "",
        "items": [],
    }


# --------------------------------------------------------------------------- #
# Settlements — actions
# --------------------------------------------------------------------------- #


@router.post(
    "/settlements/open",
    summary="Admin: open a new seller settlement period",
)
async def open_settlement(
    body: dict[str, Any],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_READ))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Insert a fresh ``OPEN`` settlement and return it (``SettlementDetailWire``)."""
    try:
        sid = str(uuid4())
        async with uow.transactional() as session:
            await _ensure_schema(session)
            await session.execute(
                text(
                    f"INSERT INTO {_SETTLE_TBL} "
                    "(id, seller_id, period_start, period_end, currency, status, notes) "
                    "VALUES (:id, :seller_id, :ps, :pe, :ccy, 'OPEN', :notes)"
                ),
                {
                    "id": sid,
                    "seller_id": str(body.get("seller_id") or "").strip(),
                    "ps": str(body.get("period_start") or "").strip(),
                    "pe": str(body.get("period_end") or "").strip(),
                    "ccy": (str(body.get("currency") or "BDT").strip().upper() or "BDT"),
                    "notes": body.get("notes"),
                },
            )
            res = await session.execute(
                text(f"SELECT * FROM {_SETTLE_TBL} WHERE id = :id"), {"id": sid}
            )
            row = res.mappings().first()
            return _settlement_row(row) if row else dict(_NOT_AVAILABLE)
    except Exception:  # noqa: BLE001 - never 500 the FE
        return dict(_NOT_AVAILABLE)


@router.post(
    "/settlements/close-period",
    summary="Admin: close all OPEN settlements matching (seller, period, currency)",
)
async def close_period(
    body: dict[str, Any],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_READ))],
    seller_id: Annotated[str, Query()],
    period_start: Annotated[str, Query()],
    period_end: Annotated[str, Query()],
    currency: Annotated[str, Query()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Close the OPEN settlement matching the period key; return the closed row."""
    try:
        async with uow.transactional() as session:
            await _ensure_schema(session)
            res = await session.execute(
                text(
                    f"UPDATE {_SETTLE_TBL} SET status = 'CLOSED', "
                    "closed_at = now(), closed_by = :by, "
                    "notes = COALESCE(:notes, notes) "
                    "WHERE seller_id = :seller_id AND period_start = :ps "
                    "AND period_end = :pe AND currency = :ccy AND status = 'OPEN' "
                    "RETURNING *"
                ),
                {
                    "by": _actor(principal),
                    "notes": body.get("notes"),
                    "seller_id": seller_id.strip(),
                    "ps": period_start.strip(),
                    "pe": period_end.strip(),
                    "ccy": currency.strip().upper(),
                },
            )
            row = res.mappings().first()
            return _settlement_row(row) if row else dict(_NOT_AVAILABLE)
    except Exception:  # noqa: BLE001
        return dict(_NOT_AVAILABLE)


@router.post(
    "/settlements/ledger/adjustments",
    summary="Admin: append a signed ADJUSTMENT ledger entry",
)
async def create_adjustment(
    body: dict[str, Any],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_READ))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Insert an ADJUSTMENT ledger row; return it (``LedgerEntryWire``)."""
    try:
        lid = str(uuid4())
        async with uow.transactional() as session:
            await _ensure_schema(session)
            await session.execute(
                text(
                    f"INSERT INTO {_LEDGER_TBL} "
                    "(id, seller_id, kind, amount, commission_amount, seller_net, "
                    " currency, occurred_at, memo, order_id, return_id) "
                    "VALUES (:id, :seller_id, 'ADJUSTMENT', :amount, :comm, :net, "
                    " :ccy, COALESCE(CAST(:occurred_at AS TIMESTAMPTZ), now()), "
                    " :memo, :order_id, :return_id)"
                ),
                {
                    "id": lid,
                    "seller_id": str(body.get("seller_id") or "").strip(),
                    "amount": str(body.get("amount") or "0"),
                    "comm": str(body.get("commission_amount") or "0"),
                    "net": str(body.get("seller_net") or "0"),
                    "ccy": (str(body.get("currency") or "BDT").strip().upper() or "BDT"),
                    "occurred_at": body.get("occurred_at"),
                    "memo": body.get("memo"),
                    "order_id": body.get("order_id"),
                    "return_id": body.get("return_id"),
                },
            )
            res = await session.execute(
                text(f"SELECT * FROM {_LEDGER_TBL} WHERE id = :id"), {"id": lid}
            )
            row = res.mappings().first()
            return _ledger_row(row) if row else dict(_NOT_AVAILABLE)
    except Exception:  # noqa: BLE001
        return dict(_NOT_AVAILABLE)


@router.post(
    "/settlements/{settlement_id}/close",
    summary="Admin: close a settlement (OPEN -> CLOSED)",
)
async def close_settlement(
    settlement_id: str,
    body: dict[str, Any],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_READ))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    try:
        sid = _coerce_uuid(settlement_id)
        async with uow.transactional() as session:
            await _ensure_schema(session)
            res = await session.execute(
                text(
                    f"UPDATE {_SETTLE_TBL} SET status = 'CLOSED', closed_at = now(), "
                    "closed_by = :by, notes = COALESCE(:notes, notes) "
                    "WHERE id = :id AND status = 'OPEN' RETURNING *"
                ),
                {"by": _actor(principal), "notes": body.get("notes"), "id": sid},
            )
            row = res.mappings().first()
            if row:
                return _settlement_row(row)
            # Idempotent / wrong-state: return current row if it exists.
            cur = await session.execute(
                text(f"SELECT * FROM {_SETTLE_TBL} WHERE id = :id"), {"id": sid}
            )
            crow = cur.mappings().first()
            return _settlement_row(crow) if crow else dict(_NOT_AVAILABLE)
    except Exception:  # noqa: BLE001
        return dict(_NOT_AVAILABLE)


@router.post(
    "/settlements/{settlement_id}/pay",
    summary="Admin: mark a settlement paid (CLOSED -> PAID)",
)
async def pay_settlement(
    settlement_id: str,
    body: dict[str, Any],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_READ))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    try:
        sid = _coerce_uuid(settlement_id)
        async with uow.transactional() as session:
            await _ensure_schema(session)
            res = await session.execute(
                text(
                    f"UPDATE {_SETTLE_TBL} SET status = 'PAID', paid_at = now(), "
                    "paid_by = :by, payout_reference = :ref, "
                    "notes = COALESCE(:notes, notes) "
                    "WHERE id = :id AND status = 'CLOSED' RETURNING *"
                ),
                {
                    "by": _actor(principal),
                    "ref": str(body.get("payout_reference") or "").strip() or None,
                    "notes": body.get("notes"),
                    "id": sid,
                },
            )
            row = res.mappings().first()
            if row:
                return _settlement_row(row)
            cur = await session.execute(
                text(f"SELECT * FROM {_SETTLE_TBL} WHERE id = :id"), {"id": sid}
            )
            crow = cur.mappings().first()
            return _settlement_row(crow) if crow else dict(_NOT_AVAILABLE)
    except Exception:  # noqa: BLE001
        return dict(_NOT_AVAILABLE)


@router.post(
    "/settlements/{settlement_id}/void",
    summary="Admin: void a settlement (OPEN/CLOSED -> VOIDED)",
)
async def void_settlement(
    settlement_id: str,
    body: dict[str, Any],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_READ))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    try:
        sid = _coerce_uuid(settlement_id)
        reason = str(body.get("reason") or "").strip()
        async with uow.transactional() as session:
            await _ensure_schema(session)
            res = await session.execute(
                text(
                    f"UPDATE {_SETTLE_TBL} SET status = 'VOIDED', voided_at = now(), "
                    "voided_by = :by, notes = COALESCE(:reason, notes) "
                    "WHERE id = :id AND status IN ('OPEN', 'CLOSED') RETURNING *"
                ),
                {"by": _actor(principal), "reason": reason or None, "id": sid},
            )
            row = res.mappings().first()
            if row:
                return _settlement_row(row)
            cur = await session.execute(
                text(f"SELECT * FROM {_SETTLE_TBL} WHERE id = :id"), {"id": sid}
            )
            crow = cur.mappings().first()
            return _settlement_row(crow) if crow else dict(_NOT_AVAILABLE)
    except Exception:  # noqa: BLE001
        return dict(_NOT_AVAILABLE)


# --------------------------------------------------------------------------- #
# Payout batches — actions (maker-checker)
# --------------------------------------------------------------------------- #


@router.post(
    "/payout-batches",
    summary="Admin: create a DRAFT payout batch from settlement ids",
)
async def create_payout_batch(
    body: dict[str, Any],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_READ))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Insert a DRAFT batch + one item per settlement id; return the batch."""
    try:
        bid = str(uuid4())
        raw_ids = body.get("settlement_ids") or []
        ids = [str(s).strip() for s in raw_ids if str(s).strip()]
        ccy = str(body.get("currency") or "BDT").strip().upper() or "BDT"
        code = str(body.get("code") or "").strip() or f"PB-{bid[:8].upper()}"
        provider = str(body.get("target_provider") or "").strip()
        async with uow.transactional() as session:
            await _ensure_schema(session)
            await session.execute(
                text(
                    f"INSERT INTO {_BATCH_TBL} "
                    "(id, code, status, currency, item_count, target_provider, "
                    " memo, created_by) "
                    "VALUES (:id, :code, 'DRAFT', :ccy, :n, :provider, :memo, :by)"
                ),
                {
                    "id": bid,
                    "code": code,
                    "ccy": ccy,
                    "n": len(ids),
                    "provider": provider,
                    "memo": body.get("memo"),
                    "by": _actor(principal),
                },
            )
            total = 0.0
            for sett_id in ids:
                # Pull the settlement net (if our settlement table has it) so the
                # batch total reflects real payable amounts; default to 0.
                amt = 0.0
                try:
                    netres = await session.execute(
                        text(
                            f"SELECT net_payable, seller_id FROM {_SETTLE_TBL} "
                            "WHERE id = :sid"
                        ),
                        {"sid": _coerce_uuid(sett_id)},
                    )
                    nrow = netres.mappings().first()
                    seller = nrow["seller_id"] if nrow else ""
                    amt = float(nrow["net_payable"]) if nrow else 0.0
                except Exception:  # noqa: BLE001
                    seller = ""
                total += amt
                await session.execute(
                    text(
                        f"INSERT INTO {_BATCH_ITEM_TBL} "
                        "(id, batch_id, settlement_id, seller_id, amount, currency, "
                        " status) "
                        "VALUES (:id, :bid, :sid, :seller, :amount, :ccy, 'PENDING')"
                    ),
                    {
                        "id": str(uuid4()),
                        "bid": bid,
                        "sid": _coerce_uuid(sett_id),
                        "seller": seller or "",
                        "amount": str(amt),
                        "ccy": ccy,
                    },
                )
            await session.execute(
                text(
                    f"UPDATE {_BATCH_TBL} SET total_amount = :total WHERE id = :id"
                ),
                {"total": str(total), "id": bid},
            )
            res = await session.execute(
                text(f"SELECT * FROM {_BATCH_TBL} WHERE id = :id"), {"id": bid}
            )
            row = res.mappings().first()
            return _batch_row(row) if row else dict(_NOT_AVAILABLE)
    except Exception:  # noqa: BLE001
        return dict(_NOT_AVAILABLE)


@router.post(
    "/payout-batches/{batch_id}/approve",
    summary="Admin: approve a payout batch (DRAFT -> APPROVED, maker-checker)",
)
async def approve_payout_batch(
    batch_id: str,
    body: dict[str, Any],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_READ))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """DRAFT -> APPROVED. Enforces maker-checker: approver != creator."""
    try:
        bid = _coerce_uuid(batch_id)
        actor = _actor(principal)
        async with uow.transactional() as session:
            await _ensure_schema(session)
            res = await session.execute(
                text(
                    f"UPDATE {_BATCH_TBL} SET status = 'APPROVED', "
                    "approved_at = now(), approved_by = :by, "
                    "memo = COALESCE(:memo, memo) "
                    "WHERE id = :id AND status = 'DRAFT' "
                    "AND (created_by IS NULL OR created_by <> :by) RETURNING *"
                ),
                {"by": actor, "memo": body.get("note"), "id": bid},
            )
            row = res.mappings().first()
            if row:
                return _batch_row(row)
            cur = await session.execute(
                text(f"SELECT * FROM {_BATCH_TBL} WHERE id = :id"), {"id": bid}
            )
            crow = cur.mappings().first()
            if crow is None:
                return dict(_NOT_AVAILABLE)
            # Maker-checker violation: surface a clean, non-500 reason.
            if crow["status"] == "DRAFT" and crow["created_by"] == actor:
                return {"ok": False, "reason": "maker_checker_violation"}
            return _batch_row(crow)
    except Exception:  # noqa: BLE001
        return dict(_NOT_AVAILABLE)


@router.post(
    "/payout-batches/{batch_id}/execute",
    summary="Admin: execute a payout batch (APPROVED -> EXECUTED)",
)
async def execute_payout_batch(
    batch_id: str,
    body: dict[str, Any],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_READ))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """APPROVED -> EXECUTED; mark items EXECUTED. Terminal batches short-circuit."""
    try:
        bid = _coerce_uuid(batch_id)
        async with uow.transactional() as session:
            await _ensure_schema(session)
            res = await session.execute(
                text(
                    f"UPDATE {_BATCH_TBL} SET status = 'EXECUTED', "
                    "executed_at = now(), executed_by = :by, "
                    "memo = COALESCE(:memo, memo) "
                    "WHERE id = :id AND status = 'APPROVED' RETURNING *"
                ),
                {"by": _actor(principal), "memo": body.get("note"), "id": bid},
            )
            row = res.mappings().first()
            if row:
                await session.execute(
                    text(
                        f"UPDATE {_BATCH_ITEM_TBL} SET status = 'EXECUTED', "
                        "executed_at = now() WHERE batch_id = :bid "
                        "AND status = 'PENDING'"
                    ),
                    {"bid": bid},
                )
                return _batch_row(row)
            cur = await session.execute(
                text(f"SELECT * FROM {_BATCH_TBL} WHERE id = :id"), {"id": bid}
            )
            crow = cur.mappings().first()
            return _batch_row(crow) if crow else dict(_NOT_AVAILABLE)
    except Exception:  # noqa: BLE001
        return dict(_NOT_AVAILABLE)


@router.post(
    "/payout-batches/{batch_id}/void",
    summary="Admin: void a payout batch (DRAFT/APPROVED -> VOIDED)",
)
async def void_payout_batch(
    batch_id: str,
    body: dict[str, Any],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_READ))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    try:
        bid = _coerce_uuid(batch_id)
        reason = str(body.get("reason") or "").strip()
        async with uow.transactional() as session:
            await _ensure_schema(session)
            res = await session.execute(
                text(
                    f"UPDATE {_BATCH_TBL} SET status = 'VOIDED', voided_at = now(), "
                    "voided_by = :by, void_reason = :reason "
                    "WHERE id = :id AND status IN ('DRAFT', 'APPROVED') RETURNING *"
                ),
                {"by": _actor(principal), "reason": reason or None, "id": bid},
            )
            row = res.mappings().first()
            if row:
                return _batch_row(row)
            cur = await session.execute(
                text(f"SELECT * FROM {_BATCH_TBL} WHERE id = :id"), {"id": bid}
            )
            crow = cur.mappings().first()
            return _batch_row(crow) if crow else dict(_NOT_AVAILABLE)
    except Exception:  # noqa: BLE001
        return dict(_NOT_AVAILABLE)
