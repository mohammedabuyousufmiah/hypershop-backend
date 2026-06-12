"""SQLA wrappers for the seo tables."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, asc, desc, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.modules.seo.models import (
    BlogPost,
    EntityFaq,
    HomepageBanner,
    SeoMetaOverride,
    SeoMetaTranslation,
    UrlRedirect,
)
from app.modules.seo.state import BlogPostStatus


# ============================================================
#  SeoMetaOverrideRepository
# ============================================================
class SeoMetaOverrideRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, *, entity_type: str, entity_key: str,
    ) -> SeoMetaOverride | None:
        return (
            await self.session.execute(
                select(SeoMetaOverride)
                .where(
                    and_(
                        SeoMetaOverride.entity_type == entity_type,
                        SeoMetaOverride.entity_key == entity_key,
                    ),
                ),
            )
        ).scalar_one_or_none()

    async def upsert(
        self, *,
        entity_type: str,
        entity_key: str,
        updated_by: UUID | None = None,
        **fields: Any,
    ) -> SeoMetaOverride:
        existing = await self.get(
            entity_type=entity_type, entity_key=entity_key,
        )
        if existing is not None:
            await self.session.execute(
                update(SeoMetaOverride)
                .where(SeoMetaOverride.id == existing.id)
                .values(updated_by=updated_by, **fields),
            )
            return await self.get(
                entity_type=entity_type, entity_key=entity_key,
            )  # type: ignore[return-value]
        row = SeoMetaOverride(
            entity_type=entity_type,
            entity_key=entity_key,
            updated_by=updated_by,
            **fields,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def delete(self, override_id: UUID) -> int:
        from sqlalchemy import delete as sa_delete
        result = await self.session.execute(
            sa_delete(SeoMetaOverride)
            .where(SeoMetaOverride.id == override_id),
        )
        return int(result.rowcount or 0)


# ============================================================
#  SeoMetaTranslationRepository  — Module 34 multi-language sprint
# ============================================================
class SeoMetaTranslationRepository:
    """CRUD for per-locale SEO translations.

    Translation rows are NEVER auto-created — they exist only when an
    operator has explicitly translated a page. Missing rows fall back to
    the override or builder default at the service layer.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, *, entity_type: str, entity_key: str, locale: str,
    ) -> SeoMetaTranslation | None:
        return (
            await self.session.execute(
                select(SeoMetaTranslation)
                .where(
                    and_(
                        SeoMetaTranslation.entity_type == entity_type,
                        SeoMetaTranslation.entity_key == entity_key,
                        SeoMetaTranslation.locale == locale,
                    ),
                ),
            )
        ).scalar_one_or_none()

    async def list_locales_for(
        self, *, entity_type: str, entity_key: str,
    ) -> list[str]:
        """Return the list of locale codes that have a translation for
        this entity. Used by the public API to build hreflang lists.
        """
        rows = (
            await self.session.execute(
                select(SeoMetaTranslation.locale)
                .where(
                    and_(
                        SeoMetaTranslation.entity_type == entity_type,
                        SeoMetaTranslation.entity_key == entity_key,
                    ),
                )
                .order_by(SeoMetaTranslation.locale),
            )
        ).all()
        return [r[0] for r in rows]

    async def upsert(
        self, *,
        entity_type: str,
        entity_key: str,
        locale: str,
        updated_by: UUID | None = None,
        **fields: Any,
    ) -> SeoMetaTranslation:
        existing = await self.get(
            entity_type=entity_type,
            entity_key=entity_key,
            locale=locale,
        )
        if existing is not None:
            await self.session.execute(
                update(SeoMetaTranslation)
                .where(SeoMetaTranslation.id == existing.id)
                .values(updated_by=updated_by, **fields),
            )
            return await self.get(
                entity_type=entity_type,
                entity_key=entity_key,
                locale=locale,
            )  # type: ignore[return-value]
        row = SeoMetaTranslation(
            entity_type=entity_type,
            entity_key=entity_key,
            locale=locale,
            updated_by=updated_by,
            **fields,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_all_for_entity(
        self, *, entity_type: str, entity_key: str,
    ) -> Sequence[SeoMetaTranslation]:
        return (
            (
                await self.session.execute(
                    select(SeoMetaTranslation)
                    .where(
                        and_(
                            SeoMetaTranslation.entity_type == entity_type,
                            SeoMetaTranslation.entity_key == entity_key,
                        ),
                    )
                    .order_by(SeoMetaTranslation.locale),
                )
            )
            .scalars()
            .all()
        )

    async def delete(self, translation_id: UUID) -> int:
        from sqlalchemy import delete as sa_delete
        result = await self.session.execute(
            sa_delete(SeoMetaTranslation)
            .where(SeoMetaTranslation.id == translation_id),
        )
        return int(result.rowcount or 0)


# ============================================================
#  HomepageBannerRepository
# ============================================================
class HomepageBannerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, banner_id: UUID) -> HomepageBanner | None:
        return await self.session.get(HomepageBanner, banner_id)

    async def list_active_now(
        self, *, limit: int = 20,
    ) -> Sequence[HomepageBanner]:
        """Active banners visible right now: is_active + valid_from/until
        windows respected.
        """
        now = utc_now()
        return (
            (
                await self.session.execute(
                    select(HomepageBanner)
                    .where(
                        and_(
                            HomepageBanner.is_active.is_(True),
                            or_(
                                HomepageBanner.valid_from.is_(None),
                                HomepageBanner.valid_from <= now,
                            ),
                            or_(
                                HomepageBanner.valid_until.is_(None),
                                HomepageBanner.valid_until > now,
                            ),
                        ),
                    )
                    .order_by(HomepageBanner.sort_order, HomepageBanner.id)
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )

    async def list_all(
        self, *, limit: int = 200,
    ) -> Sequence[HomepageBanner]:
        return (
            (
                await self.session.execute(
                    select(HomepageBanner)
                    .order_by(HomepageBanner.sort_order, HomepageBanner.id)
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )

    async def add(self, **fields: Any) -> HomepageBanner:
        row = HomepageBanner(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(self, *, banner_id: UUID, **fields: Any) -> int:
        if not fields:
            return 0
        result = await self.session.execute(
            update(HomepageBanner)
            .where(HomepageBanner.id == banner_id)
            .values(**fields),
        )
        return int(result.rowcount or 0)


# ============================================================
#  BlogPostRepository
# ============================================================
class BlogPostRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, post_id: UUID) -> BlogPost | None:
        return await self.session.get(BlogPost, post_id)

    async def get_by_slug(self, slug: str) -> BlogPost | None:
        return (
            await self.session.execute(
                select(BlogPost).where(BlogPost.slug == slug),
            )
        ).scalar_one_or_none()

    async def list_published(
        self, *, limit: int = 50,
    ) -> Sequence[BlogPost]:
        return (
            (
                await self.session.execute(
                    select(BlogPost)
                    .where(BlogPost.status == BlogPostStatus.PUBLISHED.value)
                    .order_by(desc(BlogPost.published_at))
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )

    async def list_all(
        self, *, limit: int = 200,
    ) -> Sequence[BlogPost]:
        return (
            (
                await self.session.execute(
                    select(BlogPost)
                    .order_by(desc(BlogPost.created_at))
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )

    async def add(self, **fields: Any) -> BlogPost:
        row = BlogPost(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(self, *, post_id: UUID, **fields: Any) -> int:
        if not fields:
            return 0
        result = await self.session.execute(
            update(BlogPost)
            .where(BlogPost.id == post_id)
            .values(**fields),
        )
        return int(result.rowcount or 0)


# ============================================================
#  UrlRedirectRepository
# ============================================================
class UrlRedirectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, redirect_id: UUID) -> UrlRedirect | None:
        return await self.session.get(UrlRedirect, redirect_id)

    async def get_by_from_path(
        self, from_path: str,
    ) -> UrlRedirect | None:
        return (
            await self.session.execute(
                select(UrlRedirect)
                .where(
                    and_(
                        UrlRedirect.from_path == from_path,
                        UrlRedirect.is_active.is_(True),
                    ),
                ),
            )
        ).scalar_one_or_none()

    async def list_all(
        self, *, limit: int = 200,
    ) -> Sequence[UrlRedirect]:
        return (
            (
                await self.session.execute(
                    select(UrlRedirect)
                    .order_by(desc(UrlRedirect.created_at))
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )

    async def add(self, **fields: Any) -> UrlRedirect:
        row = UrlRedirect(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def bump_hit(self, redirect_id: UUID) -> None:
        from sqlalchemy import literal_column
        await self.session.execute(
            update(UrlRedirect)
            .where(UrlRedirect.id == redirect_id)
            .values(
                last_hit_at=utc_now(),
                hit_count=UrlRedirect.hit_count + 1,
            ),
        )

    async def delete(self, redirect_id: UUID) -> int:
        from sqlalchemy import delete as sa_delete
        result = await self.session.execute(
            sa_delete(UrlRedirect)
            .where(UrlRedirect.id == redirect_id),
        )
        return int(result.rowcount or 0)


# ============================================================
#  EntityFaqRepository
# ============================================================
class EntityFaqRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, faq_id: UUID) -> EntityFaq | None:
        return await self.session.get(EntityFaq, faq_id)

    async def list_active_for(
        self, *, entity_type: str, entity_key: str, locale: str,
    ) -> Sequence[EntityFaq]:
        """Active FAQs for an entity in ``locale``, ordered for display.

        Returns an empty sequence when the entity has no active rows in
        that locale — the caller decides whether to fall back.
        """
        return (
            (
                await self.session.execute(
                    select(EntityFaq)
                    .where(
                        EntityFaq.entity_type == entity_type,
                        EntityFaq.entity_key == entity_key,
                        EntityFaq.locale == locale,
                        EntityFaq.is_active.is_(True),
                    )
                    .order_by(asc(EntityFaq.position), asc(EntityFaq.created_at)),
                )
            )
            .scalars()
            .all()
        )

    async def list_all_for(
        self, *, entity_type: str, entity_key: str,
    ) -> Sequence[EntityFaq]:
        return (
            (
                await self.session.execute(
                    select(EntityFaq)
                    .where(
                        EntityFaq.entity_type == entity_type,
                        EntityFaq.entity_key == entity_key,
                    )
                    .order_by(
                        asc(EntityFaq.locale),
                        asc(EntityFaq.position),
                        asc(EntityFaq.created_at),
                    ),
                )
            )
            .scalars()
            .all()
        )

    async def add(self, **fields: Any) -> EntityFaq:
        row = EntityFaq(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(self, *, faq_id: UUID, **fields: Any) -> int:
        if not fields:
            return 0
        result = await self.session.execute(
            update(EntityFaq)
            .where(EntityFaq.id == faq_id)
            .values(**fields),
        )
        return int(result.rowcount or 0)

    async def delete(self, faq_id: UUID) -> int:
        from sqlalchemy import delete as sa_delete
        result = await self.session.execute(
            sa_delete(EntityFaq).where(EntityFaq.id == faq_id),
        )
        return int(result.rowcount or 0)
