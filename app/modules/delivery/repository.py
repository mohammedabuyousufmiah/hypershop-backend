from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.delivery.models import DeliveryZone


class DeliveryZoneRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, zone_id: UUID) -> DeliveryZone | None:
        return await self.session.get(DeliveryZone, zone_id)

    async def get_by_code(self, code: str) -> DeliveryZone | None:
        return (
            await self.session.execute(
                select(DeliveryZone).where(DeliveryZone.code == code),
            )
        ).scalar_one_or_none()

    async def list_active(self) -> Sequence[DeliveryZone]:
        stmt = (
            select(DeliveryZone)
            .where(DeliveryZone.is_active.is_(True))
            .order_by(DeliveryZone.sort_order, DeliveryZone.name)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_all(self) -> Sequence[DeliveryZone]:
        return (
            (
                await self.session.execute(
                    select(DeliveryZone).order_by(
                        DeliveryZone.sort_order, DeliveryZone.name,
                    ),
                )
            )
            .scalars()
            .all()
        )

    async def get_default(self) -> DeliveryZone | None:
        stmt = select(DeliveryZone).where(
            DeliveryZone.is_default.is_(True),
            DeliveryZone.is_active.is_(True),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_for_address(
        self, *, city: str, postal_code: str | None,
    ) -> DeliveryZone | None:
        """Resolve a zone for a given delivery address.

        Match strategy (most-specific first):
          1. ``postal_code`` matches an active zone's ``postal_codes`` array.
          2. ``city`` matches (case-insensitive) an active zone's
             ``cities`` array.
          3. Active default zone (``is_default = true``).
          4. ``None`` (caller decides — usually error: undeliverable).

        Sort by ``sort_order`` so two competing matches resolve
        deterministically (admin can re-rank).

        Postgres-specific: uses ``unnest`` + ``EXISTS`` for the
        case-insensitive city match. Both lookups are unindexed scans
        — fine for the small zone set typical of a BD pharmacy
        (typically dozens of zones, not thousands).
        """
        # 1) postal-code match — exact, ARRAY @> ARRAY[value]
        if postal_code:
            pc = postal_code.strip()
            if pc:
                stmt = (
                    select(DeliveryZone)
                    .where(DeliveryZone.is_active.is_(True))
                    .where(DeliveryZone.postal_codes.any(pc))
                    .order_by(DeliveryZone.sort_order, DeliveryZone.name)
                    .limit(1)
                )
                hit = (await self.session.execute(stmt)).scalar_one_or_none()
                if hit is not None:
                    return hit

        # 2) city match — case-insensitive via raw SQL fragment
        if city:
            lc = city.strip().lower()
            if lc:
                from sqlalchemy import text as _text
                stmt = (
                    select(DeliveryZone)
                    .where(DeliveryZone.is_active.is_(True))
                    .where(
                        _text(
                            "EXISTS (SELECT 1 FROM unnest(delivery_zones.cities) "
                            "AS c WHERE lower(c) = :city_lc)"
                        ),
                    )
                    .params(city_lc=lc)
                    .order_by(DeliveryZone.sort_order, DeliveryZone.name)
                    .limit(1)
                )
                hit = (await self.session.execute(stmt)).scalar_one_or_none()
                if hit is not None:
                    return hit

        # 3) active default
        return await self.get_default()

    async def create(self, **fields: Any) -> DeliveryZone:
        if fields.get("is_default"):
            await self._unset_other_defaults(skip_id=None)
        zone = DeliveryZone(**fields)
        self.session.add(zone)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Delivery zone code already exists.") from e
        return zone

    async def update(self, zone_id: UUID, **fields: Any) -> DeliveryZone:
        zone = await self.session.get(DeliveryZone, zone_id)
        if zone is None:
            raise NotFoundError("Delivery zone not found.")
        if fields.get("is_default") is True:
            await self._unset_other_defaults(skip_id=zone_id)
        for k, v in fields.items():
            if v is not None:
                setattr(zone, k, v)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Delivery zone update collided.") from e
        return zone

    async def _unset_other_defaults(self, *, skip_id: UUID | None) -> None:
        stmt = update(DeliveryZone).values(is_default=False)
        if skip_id is not None:
            stmt = stmt.where(DeliveryZone.id != skip_id)
        stmt = stmt.where(DeliveryZone.is_default.is_(True))
        await self.session.execute(stmt)

    async def delete(self, zone_id: UUID) -> None:
        zone = await self.session.get(DeliveryZone, zone_id)
        if zone is None:
            raise NotFoundError("Delivery zone not found.")
        await self.session.delete(zone)
        await self.session.flush()
