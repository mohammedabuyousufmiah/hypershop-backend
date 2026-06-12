"""Async SQLAlchemy repository for the sellers tables."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.sellers.models import Seller, SellerUser


class SellerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **fields: object) -> Seller:
        s = Seller(**fields)
        self.session.add(s)
        await self.session.flush()
        return s

    async def get(self, seller_id: UUID) -> Seller | None:
        return await self.session.get(Seller, seller_id)

    async def get_by_slug(self, slug: str) -> Seller | None:
        stmt = select(Seller).where(Seller.slug == slug)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list(
        self,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[Seller], int]:
        base = select(Seller)
        if status:
            base = base.where(Seller.status == status)
        items = (
            await self.session.execute(
                base.order_by(Seller.created_at.desc())
                .offset(offset).limit(limit),
            )
        ).scalars().all()
        total_stmt = select(func.count()).select_from(Seller)
        if status:
            total_stmt = total_stmt.where(Seller.status == status)
        total = int((await self.session.execute(total_stmt)).scalar_one())
        return items, total

    async def update_fields(self, seller_id: UUID, **values: object) -> None:
        if not values:
            return
        await self.session.execute(
            update(Seller).where(Seller.id == seller_id).values(**values),
        )

    async def update_commission(
        self, seller_id: UUID, commission_percent: Decimal,
    ) -> None:
        await self.session.execute(
            update(Seller)
            .where(Seller.id == seller_id)
            .values(commission_percent=commission_percent),
        )

    # ---- seller_users ----

    async def link_user(
        self, *, seller_id: UUID, user_id: UUID, role: str,
    ) -> SellerUser:
        link = SellerUser(seller_id=seller_id, user_id=user_id, role=role)
        self.session.add(link)
        await self.session.flush()
        return link

    async def unlink_user(self, *, seller_id: UUID, user_id: UUID) -> None:
        from sqlalchemy import delete
        await self.session.execute(
            delete(SellerUser)
            .where(
                SellerUser.seller_id == seller_id,
                SellerUser.user_id == user_id,
            ),
        )

    async def get_user_link(
        self, *, user_id: UUID,
    ) -> SellerUser | None:
        stmt = select(SellerUser).where(SellerUser.user_id == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_seller_users(
        self, seller_id: UUID,
    ) -> Sequence[SellerUser]:
        stmt = select(SellerUser).where(SellerUser.seller_id == seller_id)
        return (await self.session.execute(stmt)).scalars().all()
