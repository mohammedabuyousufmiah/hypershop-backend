"""storefront_cms repositories — thin SQLAlchemy wrappers."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import asc, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.storefront_cms.models import (
    FeaturedCategory,
    NavItem,
    StaticPage,
)


class NavItemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active(self) -> list[NavItem]:
        rows = await self.session.execute(
            select(NavItem)
            .where(NavItem.is_active.is_(True))
            .order_by(asc(NavItem.sort_order), asc(NavItem.label_en)),
        )
        return list(rows.scalars().all())

    async def list_all(self) -> list[NavItem]:
        rows = await self.session.execute(
            select(NavItem).order_by(asc(NavItem.sort_order)),
        )
        return list(rows.scalars().all())

    async def get(self, nid: UUID) -> NavItem | None:
        return await self.session.get(NavItem, nid)

    async def add(self, **fields: Any) -> NavItem:
        row = NavItem(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(self, nid: UUID, **fields: Any) -> int:
        res = await self.session.execute(
            update(NavItem).where(NavItem.id == nid).values(**fields),
        )
        return int(res.rowcount or 0)

    async def delete(self, nid: UUID) -> int:
        res = await self.session.execute(
            delete(NavItem).where(NavItem.id == nid),
        )
        return int(res.rowcount or 0)


class FeaturedCategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active(self) -> list[FeaturedCategory]:
        rows = await self.session.execute(
            select(FeaturedCategory)
            .where(FeaturedCategory.is_active.is_(True))
            .order_by(asc(FeaturedCategory.sort_order)),
        )
        return list(rows.scalars().all())

    async def list_all(self) -> list[FeaturedCategory]:
        rows = await self.session.execute(
            select(FeaturedCategory).order_by(asc(FeaturedCategory.sort_order)),
        )
        return list(rows.scalars().all())

    async def get(self, fid: UUID) -> FeaturedCategory | None:
        return await self.session.get(FeaturedCategory, fid)

    async def add(self, **fields: Any) -> FeaturedCategory:
        row = FeaturedCategory(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(self, fid: UUID, **fields: Any) -> int:
        res = await self.session.execute(
            update(FeaturedCategory)
            .where(FeaturedCategory.id == fid)
            .values(**fields),
        )
        return int(res.rowcount or 0)

    async def delete(self, fid: UUID) -> int:
        res = await self.session.execute(
            delete(FeaturedCategory).where(FeaturedCategory.id == fid),
        )
        return int(res.rowcount or 0)


class StaticPageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_published(self) -> list[StaticPage]:
        rows = await self.session.execute(
            select(StaticPage)
            .where(StaticPage.is_published.is_(True))
            .order_by(asc(StaticPage.sort_order)),
        )
        return list(rows.scalars().all())

    async def list_all(self) -> list[StaticPage]:
        rows = await self.session.execute(
            select(StaticPage).order_by(asc(StaticPage.sort_order)),
        )
        return list(rows.scalars().all())

    async def get_by_slug(self, slug: str) -> StaticPage | None:
        rows = await self.session.execute(
            select(StaticPage).where(StaticPage.slug == slug),
        )
        return rows.scalar_one_or_none()

    async def get(self, pid: UUID) -> StaticPage | None:
        return await self.session.get(StaticPage, pid)

    async def add(self, **fields: Any) -> StaticPage:
        row = StaticPage(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(self, pid: UUID, **fields: Any) -> int:
        res = await self.session.execute(
            update(StaticPage).where(StaticPage.id == pid).values(**fields),
        )
        return int(res.rowcount or 0)

    async def delete(self, pid: UUID) -> int:
        res = await self.session.execute(
            delete(StaticPage).where(StaticPage.id == pid),
        )
        return int(res.rowcount or 0)
