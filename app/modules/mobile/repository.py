from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.mobile.models import (
    CustomerAddress,
    CustomerPreferences,
    DeviceToken,
)


class DeviceTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        *,
        user_id: UUID,
        kind: str,
        token: str,
        app_version: str | None,
        locale: str | None,
        last_seen_at: Any,
    ) -> DeviceToken:
        """Idempotent register. Same (user_id, token) → updates last_seen + metadata."""
        existing = (
            await self.session.execute(
                select(DeviceToken).where(
                    DeviceToken.user_id == user_id,
                    DeviceToken.token == token,
                ),
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.kind = kind
            existing.app_version = app_version
            existing.locale = locale
            existing.last_seen_at = last_seen_at
            existing.is_active = True
            await self.session.flush()
            return existing
        d = DeviceToken(
            user_id=user_id, kind=kind, token=token,
            app_version=app_version, locale=locale,
            last_seen_at=last_seen_at, is_active=True,
        )
        self.session.add(d)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("Device token already registered.") from exc
        return d

    async def list_for_user(self, user_id: UUID) -> Sequence[DeviceToken]:
        return (
            await self.session.execute(
                select(DeviceToken)
                .where(DeviceToken.user_id == user_id, DeviceToken.is_active.is_(True))
                .order_by(DeviceToken.last_seen_at.desc()),
            )
        ).scalars().all()

    async def deactivate(self, *, user_id: UUID, device_id: UUID) -> None:
        d = await self.session.get(DeviceToken, device_id)
        if d is None or d.user_id != user_id:
            raise NotFoundError("Device not found.")
        d.is_active = False
        await self.session.flush()

    async def deactivate_by_token(self, *, user_id: UUID, token: str) -> bool:
        """Deactivate a device by its push token (mobile unregister flow).

        Returns True if a matching active row was flipped, False if none.
        Idempotent — unregistering an unknown/already-off token is a no-op.
        """
        rows = (
            await self.session.execute(
                select(DeviceToken).where(
                    DeviceToken.user_id == user_id,
                    DeviceToken.token == token,
                    DeviceToken.is_active.is_(True),
                ),
            )
        ).scalars().all()
        for d in rows:
            d.is_active = False
        if rows:
            await self.session.flush()
        return bool(rows)


class CustomerAddressRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, **fields: Any) -> CustomerAddress:
        a = CustomerAddress(**fields)
        self.session.add(a)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "Cannot have more than one default address per customer.",
            ) from exc
        return a

    async def get(self, *, address_id: UUID, user_id: UUID) -> CustomerAddress | None:
        a = await self.session.get(CustomerAddress, address_id)
        if a is None or a.customer_user_id != user_id:
            return None
        return a

    async def list_for_user(self, user_id: UUID) -> Sequence[CustomerAddress]:
        return (
            await self.session.execute(
                select(CustomerAddress)
                .where(CustomerAddress.customer_user_id == user_id)
                .order_by(
                    CustomerAddress.is_default.desc(),
                    CustomerAddress.created_at.desc(),
                ),
            )
        ).scalars().all()

    async def get_default(self, user_id: UUID) -> CustomerAddress | None:
        return (
            await self.session.execute(
                select(CustomerAddress).where(
                    CustomerAddress.customer_user_id == user_id,
                    CustomerAddress.is_default.is_(True),
                ),
            )
        ).scalar_one_or_none()

    async def clear_default(self, user_id: UUID) -> None:
        await self.session.execute(
            update(CustomerAddress)
            .where(
                CustomerAddress.customer_user_id == user_id,
                CustomerAddress.is_default.is_(True),
            )
            .values(is_default=False),
        )
        await self.session.flush()

    async def update(
        self, *, address: CustomerAddress, fields: dict[str, Any],
    ) -> CustomerAddress:
        for k, v in fields.items():
            if v is not None:
                setattr(address, k, v)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "Cannot have more than one default address per customer.",
            ) from exc
        return address

    async def delete(self, address: CustomerAddress) -> None:
        await self.session.delete(address)
        await self.session.flush()


class CustomerPreferencesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(self, user_id: UUID) -> CustomerPreferences:
        """Return the user's preferences row, creating defaults on first access."""
        row = (
            await self.session.execute(
                select(CustomerPreferences).where(
                    CustomerPreferences.user_id == user_id,
                ),
            )
        ).scalar_one_or_none()
        if row is None:
            row = CustomerPreferences(user_id=user_id)
            self.session.add(row)
            try:
                await self.session.flush()
            except IntegrityError:
                # Concurrent first-access create — fall back to the existing row.
                await self.session.rollback()
                row = (
                    await self.session.execute(
                        select(CustomerPreferences).where(
                            CustomerPreferences.user_id == user_id,
                        ),
                    )
                ).scalar_one()
        return row

    async def update(self, user_id: UUID, fields: dict[str, Any]) -> CustomerPreferences:
        row = await self.get_or_create(user_id)
        for k, v in fields.items():
            setattr(row, k, v)
        await self.session.flush()
        # Refresh so server-side columns (updated_at) are populated on the
        # instance — avoids a lazy-load (MissingGreenlet) when the response
        # serializer reads updated_at after the transaction closes.
        await self.session.refresh(row)
        return row
