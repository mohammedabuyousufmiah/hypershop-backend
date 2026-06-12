"""Repositories for the payments module — thin SQLAlchemy wrappers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.payments.models import (
    PaymentAttempt,
    PaymentIntent,
    PaymentRefund,
    PaymentWebhook,
    SettlementMatch,
    SettlementReport,
)


class PaymentIntentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, intent_id: UUID) -> PaymentIntent | None:
        return await self.session.get(PaymentIntent, intent_id)

    async def get_by_provider_id(
        self, *, provider: str, provider_payment_id: str,
    ) -> PaymentIntent | None:
        stmt = (
            select(PaymentIntent)
            .where(PaymentIntent.provider == provider)
            .where(PaymentIntent.provider_payment_id == provider_payment_id)
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_order(self, order_id: UUID) -> Sequence[PaymentIntent]:
        stmt = (
            select(PaymentIntent)
            .where(PaymentIntent.order_id == order_id)
            .order_by(desc(PaymentIntent.initiated_at))
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def latest_open_for_order(
        self, order_id: UUID,
    ) -> PaymentIntent | None:
        stmt = (
            select(PaymentIntent)
            .where(PaymentIntent.order_id == order_id)
            .where(PaymentIntent.status.in_(["initiated", "authorized"]))
            .order_by(desc(PaymentIntent.initiated_at))
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(self, **fields: Any) -> PaymentIntent:
        obj = PaymentIntent(**fields)
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def list_for_admin(
        self, *,
        status: str | None = None,
        provider: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[PaymentIntent], int]:
        base = select(PaymentIntent)
        count_stmt = select(func.count()).select_from(PaymentIntent)
        if status:
            base = base.where(PaymentIntent.status == status)
            count_stmt = count_stmt.where(PaymentIntent.status == status)
        if provider:
            base = base.where(PaymentIntent.provider == provider)
            count_stmt = count_stmt.where(PaymentIntent.provider == provider)
        rows = (
            await self.session.execute(
                base.order_by(desc(PaymentIntent.initiated_at))
                    .offset(offset).limit(limit),
            )
        ).scalars().all()
        total = (await self.session.execute(count_stmt)).scalar_one()
        return rows, int(total)


class PaymentAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **fields: Any) -> PaymentAttempt:
        obj = PaymentAttempt(**fields)
        self.session.add(obj)
        await self.session.flush()
        return obj


class PaymentWebhookRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def insert_or_get_existing(
        self, *,
        provider: str,
        event_id: str,
        intent_id: UUID | None,
        raw_body: dict,
        raw_headers: dict,
        status: str,
        rejection_reason: str | None = None,
    ) -> tuple[PaymentWebhook, bool]:
        """Idempotent insert on (provider, event_id). Returns
        (row, was_inserted). Caller distinguishes "first time we saw
        this webhook" (insert) from "duplicate" (existing).
        """
        stmt = (
            pg_insert(PaymentWebhook.__table__)
            .values(
                provider=provider,
                event_id=event_id,
                intent_id=intent_id,
                raw_body=raw_body,
                raw_headers=raw_headers,
                status=status,
                rejection_reason=rejection_reason,
            )
            .on_conflict_do_nothing(
                index_elements=["provider", "event_id"],
            )
            .returning(PaymentWebhook.__table__.c.id)
        )
        result = await self.session.execute(stmt)
        row = result.first()
        if row is None:
            # Conflict — fetch the existing row
            existing = (
                await self.session.execute(
                    select(PaymentWebhook)
                    .where(PaymentWebhook.provider == provider)
                    .where(PaymentWebhook.event_id == event_id)
                    .limit(1),
                )
            ).scalar_one()
            return existing, False
        # Inserted — fetch the full row
        new = await self.session.get(PaymentWebhook, row[0])
        assert new is not None
        return new, True

    async def mark_processed(
        self, webhook: PaymentWebhook, processed_at: datetime,
    ) -> None:
        webhook.status = "processed"
        webhook.processed_at = processed_at
        await self.session.flush()

    async def mark_rejected(
        self, webhook: PaymentWebhook, reason: str,
    ) -> None:
        webhook.status = "rejected"
        webhook.rejection_reason = reason[:512]
        await self.session.flush()


class PaymentRefundRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, refund_id: UUID) -> PaymentRefund | None:
        return await self.session.get(PaymentRefund, refund_id)

    async def create(self, **fields: Any) -> PaymentRefund:
        obj = PaymentRefund(**fields)
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def list_for_intent(self, intent_id: UUID) -> Sequence[PaymentRefund]:
        stmt = (
            select(PaymentRefund)
            .where(PaymentRefund.intent_id == intent_id)
            .order_by(PaymentRefund.created_at)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def total_succeeded_for_intent(self, intent_id: UUID) -> Decimal:
        stmt = (
            select(func.coalesce(func.sum(PaymentRefund.amount), 0))
            .where(PaymentRefund.intent_id == intent_id)
            .where(PaymentRefund.status == "succeeded")
        )
        v = (await self.session.execute(stmt)).scalar_one()
        return Decimal(str(v))


# ---------------------------------------------------------------------------
# Settlement reconciliation
# ---------------------------------------------------------------------------


class SettlementReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, report_id: UUID) -> SettlementReport | None:
        return await self.session.get(SettlementReport, report_id)

    async def add(self, obj: SettlementReport) -> SettlementReport:
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def add_match(self, obj: SettlementMatch) -> SettlementMatch:
        self.session.add(obj)
        return obj

    async def matches_for_report(
        self, report_id: UUID,
    ) -> Sequence[SettlementMatch]:
        stmt = (
            select(SettlementMatch)
            .where(SettlementMatch.settlement_report_id == report_id)
            .order_by(SettlementMatch.created_at, SettlementMatch.id)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def find_intents_by_provider_refs(
        self, *, provider: str, refs: Sequence[str],
    ) -> dict[str, PaymentIntent]:
        """Bulk lookup PaymentIntents for a batch of provider refs.

        Returns a dict keyed by provider_payment_id so the caller can
        do single-pass matching without N round-trips.
        """
        if not refs:
            return {}
        stmt = (
            select(PaymentIntent)
            .where(PaymentIntent.provider == provider)
            .where(PaymentIntent.provider_payment_id.in_(list(refs)))
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return {r.provider_payment_id: r for r in rows if r.provider_payment_id}
