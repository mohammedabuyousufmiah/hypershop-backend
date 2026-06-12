"""Async CRUD for the couriers module.

No transaction management — caller's UnitOfWork owns commit/rollback.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.couriers.codes import ENV_PRODUCTION
from app.modules.couriers.models import (
    CourierCodRemittance,
    CourierCredential,
    CourierProvider,
    CourierShipment,
    CourierStatusEvent,
)


# ─── Providers ──────────────────────────────────────────────────────


async def list_providers(session: AsyncSession) -> list[CourierProvider]:
    stmt = select(CourierProvider).order_by(CourierProvider.code.asc())
    return list((await session.execute(stmt)).scalars().all())


async def get_provider(
    session: AsyncSession, code: str,
) -> CourierProvider | None:
    return await session.get(CourierProvider, code)


async def enable_provider(
    session: AsyncSession, code: str, enabled: bool,
) -> CourierProvider:
    row = await session.get(CourierProvider, code)
    if row is None:
        raise LookupError(f"Courier provider '{code}' not seeded.")
    row.is_enabled = enabled
    await session.flush()
    return row


# ─── Credentials ────────────────────────────────────────────────────


async def list_credentials(
    session: AsyncSession, provider_code: str,
) -> list[CourierCredential]:
    stmt = (
        select(CourierCredential)
        .where(CourierCredential.provider_code == provider_code)
        .order_by(CourierCredential.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def create_credential(
    session: AsyncSession, **fields,
) -> CourierCredential:
    row = CourierCredential(**fields)
    session.add(row)
    await session.flush()
    return row


async def update_credential(
    session: AsyncSession, cred_id: UUID, **fields,
) -> CourierCredential | None:
    row = await session.get(CourierCredential, cred_id)
    if row is None:
        return None
    for key, value in fields.items():
        if value is not None and hasattr(row, key):
            setattr(row, key, value)
    await session.flush()
    return row


async def get_credential(
    session: AsyncSession, cred_id: UUID,
) -> CourierCredential | None:
    return await session.get(CourierCredential, cred_id)


async def get_active_credential(
    session: AsyncSession,
    provider_code: str,
    env: str = ENV_PRODUCTION,
) -> CourierCredential | None:
    stmt = (
        select(CourierCredential)
        .where(
            CourierCredential.provider_code == provider_code,
            CourierCredential.environment == env,
            CourierCredential.is_active.is_(True),
        )
        .order_by(CourierCredential.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ─── Shipments ──────────────────────────────────────────────────────


async def create_shipment_row(
    session: AsyncSession, **fields,
) -> CourierShipment:
    row = CourierShipment(**fields)
    session.add(row)
    await session.flush()
    return row


async def get_shipment(
    session: AsyncSession, shipment_id: UUID,
) -> CourierShipment | None:
    return await session.get(CourierShipment, shipment_id)


async def get_shipment_by_provider_id(
    session: AsyncSession,
    provider_code: str,
    provider_shipment_id: str,
) -> CourierShipment | None:
    stmt = select(CourierShipment).where(
        CourierShipment.provider_code == provider_code,
        CourierShipment.provider_shipment_id == provider_shipment_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def update_shipment(
    session: AsyncSession, shipment_id: UUID, **fields,
) -> CourierShipment | None:
    row = await session.get(CourierShipment, shipment_id)
    if row is None:
        return None
    for key, value in fields.items():
        if value is not None and hasattr(row, key):
            setattr(row, key, value)
    await session.flush()
    return row


async def list_shipments_for_order(
    session: AsyncSession, order_id: UUID,
) -> list[CourierShipment]:
    stmt = (
        select(CourierShipment)
        .where(CourierShipment.order_id == order_id)
        .order_by(CourierShipment.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_shipments(
    session: AsyncSession,
    *,
    status: str | None = None,
    provider_code: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[CourierShipment], int]:
    base = select(CourierShipment)
    if status is not None:
        base = base.where(CourierShipment.status == status)
    if provider_code is not None:
        base = base.where(CourierShipment.provider_code == provider_code)
    total = (
        await session.execute(
            select(func.count()).select_from(base.subquery()),
        )
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                base.order_by(CourierShipment.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
    )
    return rows, int(total)


# ─── Status events ──────────────────────────────────────────────────


async def record_status_event(
    session: AsyncSession,
    *,
    shipment_id: UUID | None,
    provider_code: str,
    event_type: str,
    mapped_status: str | None,
    raw_payload: dict | None,
    provider_shipment_id: str | None = None,
) -> CourierStatusEvent:
    row = CourierStatusEvent(
        shipment_id=shipment_id,
        provider_code=provider_code,
        provider_shipment_id=provider_shipment_id,
        event_type=event_type,
        mapped_status=mapped_status,
        raw_payload=raw_payload,
    )
    session.add(row)
    await session.flush()
    return row


async def list_status_events(
    session: AsyncSession, shipment_id: UUID, limit: int = 50,
) -> list[CourierStatusEvent]:
    stmt = (
        select(CourierStatusEvent)
        .where(CourierStatusEvent.shipment_id == shipment_id)
        .order_by(CourierStatusEvent.received_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


# ─── COD remittances ────────────────────────────────────────────────


async def record_cod_remittance(
    session: AsyncSession, **fields,
) -> CourierCodRemittance:
    row = CourierCodRemittance(**fields)
    session.add(row)
    await session.flush()
    return row


async def list_pending_remittances(
    session: AsyncSession, provider_code: str,
) -> list[CourierCodRemittance]:
    stmt = (
        select(CourierCodRemittance)
        .where(
            CourierCodRemittance.provider_code == provider_code,
            CourierCodRemittance.status == "pending",
        )
        .order_by(CourierCodRemittance.created_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())
