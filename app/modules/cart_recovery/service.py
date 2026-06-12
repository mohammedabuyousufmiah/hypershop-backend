"""Orchestration — pick channel, suppression-check, render, send, persist."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.cart_recovery import dispatch as dispatch_mod
from app.modules.cart_recovery import repository as repo
from app.modules.cart_recovery import templates as tpl
from app.modules.cart_recovery.codes import (
    CHANNEL_EMAIL,
    CHANNEL_PUSH,
    CHANNEL_WHATSAPP,
    MAX_SENDS_PER_CUSTOMER_WEEK,
    RECENT_PURCHASE_SUPPRESS_HOURS,
    STATUS_FAILED,
    STATUS_LOG_ONLY,
    STATUS_SENT,
    STATUS_SUPPRESSED,
    SUPPRESS_FREQUENCY_CAP,
    SUPPRESS_RECENT_PURCHASE,
)

_log = get_logger("hypershop.cart_recovery.service")

CART_BASE_URL = "https://hypershop.com.bd/cart"
HOME_BASE_URL = "https://hypershop.com.bd"


def _pick_channel(*, phone: str | None, email: str | None,
                  user_id: UUID | None) -> tuple[str, str] | None:
    """Returns (channel, recipient) or None when no reachable channel."""
    if phone:
        return (CHANNEL_WHATSAPP, phone)
    if email:
        return (CHANNEL_EMAIL, email)
    if user_id is not None:
        return (CHANNEL_PUSH, str(user_id))
    return None


async def dispatch_for_cart(
    session: AsyncSession,
    *,
    cart_id: UUID,
    customer_user_id: UUID | None,
    email: str | None,
    phone: str | None,
    customer_name: str | None,
    item_count: int,
    cart_total_minor: int,
    milestone: str,
    locale: str = "bn",
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    """Send one cart-recovery message for a milestone; persists audit row."""
    # Guard against double-dispatch even if caller misses the WHERE clause.
    existing = await repo.get_dispatch_by_cart_and_milestone(
        session, cart_id, milestone,
    )
    if existing is not None:
        return {"skipped": True, "reason": "already_dispatched"}

    picked = _pick_channel(
        phone=phone, email=email, user_id=customer_user_id,
    )
    if picked is None:
        return {"skipped": True, "reason": "no_channel"}
    channel, recipient = picked

    # Recent purchase suppression — guest carts (no user_id) skip this.
    if customer_user_id is not None and await repo.has_recent_order(
        session, customer_user_id, RECENT_PURCHASE_SUPPRESS_HOURS,
    ):
        await repo.create_dispatch(
            session,
            cart_id=cart_id,
            customer_user_id=customer_user_id,
            milestone=milestone,
            channel=channel,
            template_code=f"{milestone}_{channel}_{locale}",
            locale=locale,
            recipient=recipient,
            status=STATUS_SUPPRESSED,
            suppression_reason=SUPPRESS_RECENT_PURCHASE,
        )
        return {"status": STATUS_SUPPRESSED, "reason": SUPPRESS_RECENT_PURCHASE}

    # Opt-out / bounce / manual suppressions.
    suppressed, reason = await repo.is_suppressed(
        session,
        customer_user_id=customer_user_id,
        channel=channel,
        email=email,
        phone=phone,
    )
    if suppressed:
        await repo.create_dispatch(
            session,
            cart_id=cart_id,
            customer_user_id=customer_user_id,
            milestone=milestone,
            channel=channel,
            template_code=f"{milestone}_{channel}_{locale}",
            locale=locale,
            recipient=recipient,
            status=STATUS_SUPPRESSED,
            suppression_reason=reason or "opted_out",
        )
        return {"status": STATUS_SUPPRESSED, "reason": reason}

    # Frequency cap — last 7 days.
    if customer_user_id is not None:
        since = datetime.now(timezone.utc) - timedelta(days=7)
        recent = await repo.count_recent_sends(session, customer_user_id, since)
        if recent >= MAX_SENDS_PER_CUSTOMER_WEEK:
            await repo.create_dispatch(
                session,
                cart_id=cart_id,
                customer_user_id=customer_user_id,
                milestone=milestone,
                channel=channel,
                template_code=f"{milestone}_{channel}_{locale}",
                locale=locale,
                recipient=recipient,
                status=STATUS_SUPPRESSED,
                suppression_reason=SUPPRESS_FREQUENCY_CAP,
            )
            return {"status": STATUS_SUPPRESSED, "reason": SUPPRESS_FREQUENCY_CAP}

    ctx = {
        "customer_name": customer_name or "Customer",
        "item_count": item_count,
        "cart_total": f"{cart_total_minor / 100:.2f}",
        "cart_url": f"{CART_BASE_URL}/{cart_id}",
        "home_url": HOME_BASE_URL,
    }
    rendered = tpl.render(milestone, channel, locale, ctx)

    result = await _send(channel, recipient, rendered)

    sent_at = (
        datetime.now(timezone.utc)
        if result["status"] in (STATUS_SENT, STATUS_LOG_ONLY) else None
    )
    await repo.create_dispatch(
        session,
        cart_id=cart_id,
        customer_user_id=customer_user_id,
        milestone=milestone,
        channel=channel,
        template_code=rendered["template_code"],
        locale=locale,
        recipient=recipient,
        status=result["status"],
        failure_reason=(
            result.get("reason") if result["status"] == STATUS_FAILED else None
        ),
        sent_at=sent_at,
    )
    return {"status": result["status"], "channel": channel}


async def dispatch_for_winback(
    session: AsyncSession,
    *,
    customer_user_id: UUID,
    email: str | None,
    phone: str | None,
    customer_name: str | None,
    milestone: str,
    locale: str = "bn",
    last_order_at: datetime | None = None,
) -> dict[str, Any]:
    """Win-back send — same suppression rules minus recent-purchase."""
    picked = _pick_channel(
        phone=phone, email=email, user_id=customer_user_id,
    )
    if picked is None:
        return {"skipped": True, "reason": "no_channel"}
    channel, recipient = picked

    suppressed, reason = await repo.is_suppressed(
        session,
        customer_user_id=customer_user_id,
        channel=channel,
        email=email,
        phone=phone,
    )
    if suppressed:
        return {"status": STATUS_SUPPRESSED, "reason": reason}

    since = datetime.now(timezone.utc) - timedelta(days=7)
    recent = await repo.count_recent_sends(session, customer_user_id, since)
    if recent >= MAX_SENDS_PER_CUSTOMER_WEEK:
        return {"status": STATUS_SUPPRESSED, "reason": SUPPRESS_FREQUENCY_CAP}

    ctx = {
        "customer_name": customer_name or "Customer",
        "home_url": HOME_BASE_URL,
    }
    rendered = tpl.render(milestone, channel, locale, ctx)
    result = await _send(channel, recipient, rendered)

    # M3.D: cart_id is now nullable. Persist the winback dispatch row
    # with cart_id=NULL so the SQL listing query can use its
    # NOT EXISTS gate on (customer_user_id, milestone) to dedupe, and
    # so the admin dashboard can show winback throughput + recovery
    # attribution alongside cart reminders.
    dispatch_row = await repo.create_dispatch(
        session,
        cart_id=None,
        customer_user_id=customer_user_id,
        milestone=milestone,
        channel=channel,
        template_code=rendered["template_code"],
        locale=locale,
        recipient=recipient,
        status=result["status"],
        suppression_reason=None,
        failure_reason=result.get("reason") if result["status"] == STATUS_FAILED else None,
        sent_at=datetime.now(timezone.utc) if result["status"] in (STATUS_SENT, STATUS_LOG_ONLY) else None,
    )
    _log.info(
        "cart_recovery_winback_dispatched",
        user=str(customer_user_id),
        milestone=milestone,
        channel=channel,
        status=result["status"],
        dispatch_id=int(dispatch_row.id),
    )
    return {
        "status": result["status"],
        "channel": channel,
        "dispatch_id": int(dispatch_row.id),
    }


async def _send(channel: str, recipient: str, rendered: dict) -> dict:
    """Route to the channel adapter; always returns a dict."""
    if channel == CHANNEL_WHATSAPP:
        return await dispatch_mod.send_whatsapp(
            to_phone=recipient, body=rendered["body"],
        )
    if channel == CHANNEL_EMAIL:
        return await dispatch_mod.send_email(
            to_email=recipient,
            subject=rendered.get("subject") or "Hypershop",
            body=rendered["body"],
        )
    if channel == CHANNEL_PUSH:
        return await dispatch_mod.send_push(
            user_id=recipient,
            title=rendered.get("subject") or "Hypershop",
            body=rendered["body"],
        )
    return {"status": STATUS_FAILED, "provider_id": None, "reason": "unknown_channel"}


async def attribute_recovery(
    session: AsyncSession,
    *,
    customer_user_id: UUID,
    order_id: UUID,
    ordered_at: datetime,
) -> int:
    """Best-effort: stamp recovery on dispatches in last 7d for this user."""
    try:
        return await repo.record_recovery(
            session,
            customer_user_id=customer_user_id,
            order_id=order_id,
            ordered_at=ordered_at,
        )
    except Exception as e:  # noqa: BLE001
        _log.warning(
            "cart_recovery_attribution_failed user=%s order=%s err=%s",
            customer_user_id, order_id, e,
        )
        return 0
