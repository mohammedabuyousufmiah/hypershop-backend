"""SQLAlchemy models for storefront_cms. See alembic 0078 for schema."""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class NavItem(Base, TimestampMixin):
    __tablename__ = "storefront_nav_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    label_en: Mapped[str] = mapped_column(String(80), nullable=False)
    label_bn: Mapped[str | None] = mapped_column(String(80), nullable=True)
    href: Mapped[str] = mapped_column(String(255), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    open_in_new_tab: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )


class FeaturedCategory(Base, TimestampMixin):
    __tablename__ = "storefront_featured_categories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    category_slug: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True,
    )
    display_label_en: Mapped[str | None] = mapped_column(String(80), nullable=True)
    display_label_bn: Mapped[str | None] = mapped_column(String(80), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    badge_text: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class StaticPage(Base, TimestampMixin):
    __tablename__ = "storefront_static_pages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    title_en: Mapped[str] = mapped_column(String(160), nullable=False)
    title_bn: Mapped[str | None] = mapped_column(String(160), nullable=True)
    body_md_en: Mapped[str] = mapped_column(Text, nullable=False)
    body_md_bn: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_in_footer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
