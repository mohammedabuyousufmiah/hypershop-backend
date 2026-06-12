"""Subscriptions service — Module 49.

Two responsibilities:
1. ``cadence_to_timedelta(cadence)`` — turn a cadence enum into a
   timedelta for next_charge_at scheduling.
2. ``run_due_cycles(session)`` — picks up active subscriptions whose
   next_charge_at <= now(), creates a real Hypershop order for each
   via OrderService, records a subscription_cycles row, bumps
   next_charge_at by cadence. Auto-pauses to 'payment_failed' after
   3 consecutive failures so we don't keep retrying indefinitely.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text as _t
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

_log = get_logger("hypershop.subscriptions.service")


CADENCE_DAYS = {
    "weekly": 7,
    "biweekly": 14,
    "monthly": 30,
    "quarterly": 91,
}


def cadence_to_timedelta(cadence: str) -> timedelta:
    return timedelta(days=CADENCE_DAYS.get(cadence, 30))


async def claim_due_subscriptions(
    session: AsyncSession, *, batch_limit: int = 50,
) -> list[Any]:
    """Find subscriptions due now AND lock them with SKIP LOCKED so
    parallel workers don't double-charge.
    """
    rows = (
        await session.execute(
            _t(
                "SELECT id, customer_user_id, variant_id, quantity, cadence, "
                "       payment_method, delivery_address, notes, currency, "
                "       cycle_count, consecutive_failures "
                "FROM subscriptions "
                "WHERE status = 'active' AND next_charge_at <= now() "
                "ORDER BY next_charge_at ASC LIMIT :lim "
                "FOR UPDATE SKIP LOCKED"
            ),
            {"lim": batch_limit},
        )
    ).all()
    return rows


async def create_cycle(
    session: AsyncSession,
    *,
    subscription: Any,
    order_id: UUID | None,
    ok: bool,
    error: str | None = None,
) -> None:
    """Insert a subscription_cycles row + advance subscription state."""
    new_cycle_num = int(subscription[9]) + 1
    await session.execute(
        _t(
            "INSERT INTO subscription_cycles "
            "(id, subscription_id, cycle_number, order_id, status, error_message) "
            "VALUES (gen_random_uuid(), :sid, :n, :oid, :st, :err)"
        ),
        {
            "sid": subscription[0], "n": new_cycle_num,
            "oid": order_id,
            "st": "order_placed" if ok else "order_failed",
            "err": error,
        },
    )
    # Bump next_charge_at + counters
    cadence = subscription[4]
    new_failures = 0 if ok else int(subscription[10]) + 1
    new_status = "payment_failed" if new_failures >= 3 else "active"
    delta_days = CADENCE_DAYS.get(cadence, 30)
    # Two separate binds for new_status — asyncpg can't infer the
    # type when the same parameter is compared against a literal in
    # one place and assigned directly in another. Bind it twice.
    await session.execute(
        _t(
            f"UPDATE subscriptions SET "
            f"  cycle_count = cycle_count + 1, "
            f"  consecutive_failures = :f, "
            f"  status = CAST(:new_status AS varchar), "
            f"  last_charged_at = CASE WHEN :ok THEN now() ELSE last_charged_at END, "
            f"  next_charge_at = next_charge_at + INTERVAL '{delta_days} days', "
            f"  paused_reason = CASE WHEN :failure_status = 'payment_failed' "
            f"                       THEN 'auto: 3 consecutive failures' "
            f"                       ELSE paused_reason END, "
            f"  updated_at = now() "
            f"WHERE id = :sid"
        ),
        {
            "f": new_failures,
            "new_status": new_status,
            "failure_status": new_status,
            "ok": ok,
            "sid": subscription[0],
        },
    )
    # Activate pending_activation on first successful cycle
    if ok and int(subscription[9]) == 0:
        await session.execute(
            _t(
                "UPDATE subscriptions SET status = 'active' "
                "WHERE id = :sid AND status = 'pending_activation'"
            ),
            {"sid": subscription[0]},
        )


async def charge_one_subscription(
    session: AsyncSession, *, sub_row: Any,
) -> tuple[bool, UUID | None, str | None]:
    """Place an order via Hypershop's OrderService for this sub's
    variant + delivery address. Returns (ok, order_id, error)."""
    from app.modules.orders.service import OrderService
    sub_id, customer_id, variant_id, qty, cadence, pay_method, address, notes, currency, _cc, _cf = sub_row
    # Synthetic customer principal so the order belongs to the subscriber
    customer_principal = type(
        "SubCustomerPrincipal", (), {
            "user_id": customer_id, "session_id": None,
            "roles": frozenset({"customer"}),
            "permissions": frozenset({"order.place", "cart.use"}),
            "has_permission": staticmethod(lambda p: True),
            "has_role": staticmethod(lambda r: r == "customer"),
            "is_system": False,
        },
    )()
    try:
        svc = OrderService(session)
        order = await svc.place_order(
            principal=customer_principal,
            items=[{"variant_id": variant_id, "quantity": qty}],
            payment_method=pay_method,
            delivery_address=dict(address),
            notes=notes or f"Subscription #{sub_id} auto-order",
            currency=currency or "BDT",
        )
        return True, order.id, None
    except Exception as e:  # noqa: BLE001
        return False, None, str(e)[:1000]
