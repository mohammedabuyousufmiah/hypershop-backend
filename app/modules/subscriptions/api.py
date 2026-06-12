"""Subscriptions HTTP API — Module 49.

Customer-facing under `/api/v1/subscriptions/*` (customer permission).
Admin-facing under `/api/v1/admin/subscriptions/*` (admin permission).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import Field
from sqlalchemy import text as _t

from app.core.audit.service import record_audit
from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.validation import StrictModel
from app.modules.orders.schemas import DeliveryAddress
from app.modules.subscriptions import service as sub_svc

_CUSTOMER = "order.place"
_ADMIN = "order.read.any"

_log = get_logger("hypershop.subscriptions.api")

customer_router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])
admin_router = APIRouter(prefix="/admin/subscriptions", tags=["admin-subscriptions"])


# ============================================================== Schemas
class SubscriptionCreate(StrictModel):
    variant_id: UUID
    quantity: int = Field(..., ge=1, le=100)
    cadence: str = Field(..., pattern=r"^(weekly|biweekly|monthly|quarterly)$")
    payment_method: str = Field(..., pattern=r"^(cod|online)$")
    delivery_address: DeliveryAddress
    notes: str | None = Field(default=None, max_length=2048)
    start_at: datetime | None = None  # default: now


class SubscriptionUpdate(StrictModel):
    quantity: int | None = Field(default=None, ge=1, le=100)
    cadence: str | None = Field(default=None, pattern=r"^(weekly|biweekly|monthly|quarterly)$")
    delivery_address: DeliveryAddress | None = None
    notes: str | None = Field(default=None, max_length=2048)


class PauseRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=500)


# ============================================================== Helpers
_SUB_COLS = (
    "id, customer_user_id, variant_id, quantity, cadence, status, "
    "payment_method, delivery_address, notes, currency, "
    "next_charge_at, last_charged_at, consecutive_failures, "
    "cycle_count, paused_reason, cancelled_at, created_at, updated_at"
)


def _row_to_dict(r: Any) -> dict[str, Any]:
    return {
        "id": str(r[0]), "customer_user_id": str(r[1]),
        "variant_id": str(r[2]), "quantity": int(r[3]),
        "cadence": r[4], "status": r[5], "payment_method": r[6],
        "delivery_address": r[7], "notes": r[8], "currency": r[9],
        "next_charge_at": r[10], "last_charged_at": r[11],
        "consecutive_failures": int(r[12]), "cycle_count": int(r[13]),
        "paused_reason": r[14], "cancelled_at": r[15],
        "created_at": r[16], "updated_at": r[17],
    }


# ============================================================== Customer routes
@customer_router.post(
    "",
    status_code=201,
    summary="Create a recurring subscription on a variant",
    dependencies=[Depends(requires_permission(_CUSTOMER))],
)
async def create_subscription(
    body: SubscriptionCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    next_at = body.start_at or datetime.now(timezone.utc)
    async with uow.transactional() as session:
        # Verify variant exists + is active
        v = (
            await session.execute(
                _t(
                    "SELECT pv.id, p.status FROM product_variants pv "
                    "JOIN products p ON p.id = pv.product_id "
                    "WHERE pv.id = :v"
                ),
                {"v": body.variant_id},
            )
        ).first()
        if v is None:
            raise NotFoundError("Variant not found")
        if v[1] != "active":
            raise BusinessRuleError("Variant's product is not active")
        r = await session.execute(
            _t(
                f"""
                INSERT INTO subscriptions
                  (id, customer_user_id, variant_id, quantity, cadence,
                   payment_method, delivery_address, notes, next_charge_at)
                VALUES
                  (gen_random_uuid(), :u, :v, :q, :c, :pm,
                   CAST(:addr AS json), :n, :nx)
                RETURNING {_SUB_COLS}
                """,
            ),
            {
                "u": principal.user_id, "v": body.variant_id,
                "q": body.quantity, "c": body.cadence,
                "pm": body.payment_method,
                "addr": json.dumps(body.delivery_address.model_dump(mode="json")),
                "n": body.notes, "nx": next_at,
            },
        )
        row = r.first()
        await record_audit(
            actor=principal, action="subscriptions.created",
            resource_type="subscriptions", resource_id=row[0],
            metadata={"variant_id": str(body.variant_id), "cadence": body.cadence},
        )
        return _row_to_dict(row)


@customer_router.get(
    "/me",
    dependencies=[Depends(requires_permission(_CUSTOMER))],
)
async def list_mine(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    f"SELECT {_SUB_COLS} FROM subscriptions "
                    f"WHERE customer_user_id = :u ORDER BY created_at DESC"
                ),
                {"u": principal.user_id},
            )
        ).all()
        return [_row_to_dict(r) for r in rows]


@customer_router.get(
    "/{sid}",
    dependencies=[Depends(requires_permission(_CUSTOMER))],
)
async def get_mine(
    sid: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = (
            await session.execute(
                _t(
                    f"SELECT {_SUB_COLS} FROM subscriptions "
                    f"WHERE id = :s AND customer_user_id = :u"
                ),
                {"s": sid, "u": principal.user_id},
            )
        ).first()
        if r is None:
            raise NotFoundError("Subscription not found")
        # Pull cycle history
        cycles = (
            await session.execute(
                _t(
                    "SELECT id, cycle_number, order_id, status, error_message, charged_at "
                    "FROM subscription_cycles WHERE subscription_id = :s "
                    "ORDER BY cycle_number DESC LIMIT 24"
                ),
                {"s": sid},
            )
        ).all()
        return {
            **_row_to_dict(r),
            "cycles": [
                {
                    "id": str(c[0]), "cycle_number": int(c[1]),
                    "order_id": str(c[2]) if c[2] else None,
                    "status": c[3], "error_message": c[4],
                    "charged_at": c[5],
                }
                for c in cycles
            ],
        }


@customer_router.patch(
    "/{sid}",
    dependencies=[Depends(requires_permission(_CUSTOMER))],
)
async def update_mine(
    sid: Annotated[UUID, Path(...)],
    body: SubscriptionUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise ValidationError("nothing to update")
    parts = []
    params: dict[str, Any] = {"s": sid, "u": principal.user_id}
    for k, v in fields.items():
        if k == "delivery_address":
            parts.append("delivery_address = CAST(:da AS json)")
            params["da"] = json.dumps(v.model_dump(mode="json") if hasattr(v, "model_dump") else v)
        else:
            parts.append(f"{k} = :{k}")
            params[k] = v
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                f"UPDATE subscriptions SET {', '.join(parts)}, updated_at = now() "
                f"WHERE id = :s AND customer_user_id = :u "
                f"AND status IN ('pending_activation','active','paused') "
                f"RETURNING {_SUB_COLS}"
            ),
            params,
        )
        row = r.first()
        if row is None:
            raise NotFoundError("Subscription not found / not editable")
        return _row_to_dict(row)


@customer_router.post(
    "/{sid}/pause",
    dependencies=[Depends(requires_permission(_CUSTOMER))],
)
async def pause(
    sid: Annotated[UUID, Path(...)],
    body: PauseRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                f"UPDATE subscriptions SET status = 'paused', "
                f"paused_reason = :r, updated_at = now() "
                f"WHERE id = :s AND customer_user_id = :u AND status = 'active' "
                f"RETURNING {_SUB_COLS}"
            ),
            {"s": sid, "u": principal.user_id, "r": body.reason},
        )
        row = r.first()
        if row is None:
            raise BusinessRuleError("Only active subscriptions can be paused")
        await record_audit(
            actor=principal, action="subscriptions.paused",
            resource_type="subscriptions", resource_id=sid,
        )
        return _row_to_dict(row)


@customer_router.post(
    "/{sid}/resume",
    dependencies=[Depends(requires_permission(_CUSTOMER))],
)
async def resume(
    sid: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                f"UPDATE subscriptions SET status = 'active', "
                f"paused_reason = NULL, "
                f"next_charge_at = GREATEST(next_charge_at, now()), "
                f"updated_at = now() "
                f"WHERE id = :s AND customer_user_id = :u "
                f"AND status IN ('paused','payment_failed') "
                f"RETURNING {_SUB_COLS}"
            ),
            {"s": sid, "u": principal.user_id},
        )
        row = r.first()
        if row is None:
            raise BusinessRuleError("Only paused / failed subscriptions can be resumed")
        # Reset failure counter on resume
        await session.execute(
            _t("UPDATE subscriptions SET consecutive_failures = 0 WHERE id = :s"),
            {"s": sid},
        )
        await record_audit(
            actor=principal, action="subscriptions.resumed",
            resource_type="subscriptions", resource_id=sid,
        )
        return _row_to_dict(row)


@customer_router.post(
    "/{sid}/cancel",
    dependencies=[Depends(requires_permission(_CUSTOMER))],
)
async def cancel(
    sid: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                f"UPDATE subscriptions SET status = 'cancelled', "
                f"cancelled_at = now(), updated_at = now() "
                f"WHERE id = :s AND customer_user_id = :u "
                f"AND status NOT IN ('cancelled') "
                f"RETURNING {_SUB_COLS}"
            ),
            {"s": sid, "u": principal.user_id},
        )
        row = r.first()
        if row is None:
            raise BusinessRuleError("Subscription already cancelled or not found")
        await record_audit(
            actor=principal, action="subscriptions.cancelled",
            resource_type="subscriptions", resource_id=sid,
        )
        return _row_to_dict(row)


# ============================================================== Admin routes
@admin_router.get(
    "",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def admin_list(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    sql = f"SELECT {_SUB_COLS} FROM subscriptions "
    params: dict[str, Any] = {"lim": limit}
    if status_filter:
        sql += "WHERE status = :st "
        params["st"] = status_filter
    sql += "ORDER BY created_at DESC LIMIT :lim"
    async with uow.transactional() as session:
        rows = (await session.execute(_t(sql), params)).all()
        return [_row_to_dict(r) for r in rows]


@admin_router.post(
    "/{sid}/force-charge",
    summary="Admin manually triggers one cycle (testing / recovery)",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def force_charge(
    sid: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    """Loads the subscription, places one order, records the cycle.
    Skips the next_charge_at gate so admins can fire on demand."""
    async with uow.transactional() as session:
        sub = (
            await session.execute(
                _t(
                    "SELECT id, customer_user_id, variant_id, quantity, cadence, "
                    "       payment_method, delivery_address, notes, currency, "
                    "       cycle_count, consecutive_failures "
                    "FROM subscriptions WHERE id = :s FOR UPDATE"
                ),
                {"s": sid},
            )
        ).first()
        if sub is None:
            raise NotFoundError("Subscription not found")
        ok, order_id, err = await sub_svc.charge_one_subscription(
            session, sub_row=sub,
        )
        await sub_svc.create_cycle(
            session, subscription=sub, order_id=order_id, ok=ok, error=err,
        )
        await record_audit(
            actor=principal, action="subscriptions.force_charge",
            resource_type="subscriptions", resource_id=sid,
            metadata={"ok": ok, "order_id": str(order_id) if order_id else None, "error": err},
        )
    return {
        "subscription_id": str(sid), "ok": ok,
        "order_id": str(order_id) if order_id else None, "error": err,
    }
