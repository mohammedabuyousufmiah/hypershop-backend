"""Pydantic schemas for storefront_cms wire."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Nav items ----------
class NavItemResponse(_Base):
    id: UUID
    label_en: str
    label_bn: Optional[str] = None
    href: str
    icon: Optional[str] = None
    sort_order: int
    is_active: bool
    open_in_new_tab: bool


class NavItemUpsert(BaseModel):
    label_en: str = Field(min_length=1, max_length=80)
    label_bn: Optional[str] = Field(default=None, max_length=80)
    href: str = Field(min_length=1, max_length=255)
    icon: Optional[str] = Field(default=None, max_length=40)
    sort_order: int = Field(default=0, ge=0, le=9999)
    is_active: bool = True
    open_in_new_tab: bool = False


# ---------- Featured categories ----------
class FeaturedCategoryResponse(_Base):
    id: UUID
    category_slug: str
    display_label_en: Optional[str] = None
    display_label_bn: Optional[str] = None
    image_url: Optional[str] = None
    badge_text: Optional[str] = None
    sort_order: int
    is_active: bool


class FeaturedCategoryUpsert(BaseModel):
    category_slug: str = Field(min_length=1, max_length=120)
    display_label_en: Optional[str] = Field(default=None, max_length=80)
    display_label_bn: Optional[str] = Field(default=None, max_length=80)
    image_url: Optional[str] = Field(default=None, max_length=512)
    badge_text: Optional[str] = Field(default=None, max_length=40)
    sort_order: int = Field(default=0, ge=0, le=9999)
    is_active: bool = True


# ---------- Static pages ----------
class StaticPageListItem(_Base):
    id: UUID
    slug: str
    title_en: str
    title_bn: Optional[str] = None
    is_published: bool
    show_in_footer: bool
    sort_order: int
    updated_at: datetime


class StaticPageResponse(_Base):
    id: UUID
    slug: str
    title_en: str
    title_bn: Optional[str] = None
    body_md_en: str
    body_md_bn: Optional[str] = None
    meta_description: Optional[str] = None
    is_published: bool
    show_in_footer: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class StaticPageUpsert(BaseModel):
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9-]+$")
    title_en: str = Field(min_length=1, max_length=160)
    title_bn: Optional[str] = Field(default=None, max_length=160)
    body_md_en: str = Field(min_length=1)
    body_md_bn: Optional[str] = None
    meta_description: Optional[str] = Field(default=None, max_length=255)
    is_published: bool = True
    show_in_footer: bool = True
    sort_order: int = Field(default=0, ge=0, le=9999)


# ---------- Unified layout response ----------
class StorefrontBanner(_Base):
    """Re-shape of the seo HomepageBanner for the unified payload."""
    id: UUID
    title: Optional[str] = None
    subtitle: Optional[str] = None
    image_url: Optional[str] = None
    mobile_image_url: Optional[str] = None
    target_url: Optional[str] = None
    alt_text: Optional[str] = None
    sort_order: int


class StorefrontLayoutResponse(BaseModel):
    """Single payload the storefront fetches with tag=['storefront']."""
    version: int
    banners: list[StorefrontBanner]
    nav_items: list[NavItemResponse]
    featured_categories: list[FeaturedCategoryResponse]
    footer_pages: list[StaticPageListItem]
