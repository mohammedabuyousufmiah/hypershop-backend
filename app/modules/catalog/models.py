from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class ProductStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class Brand(Base, TimestampMixin):
    __tablename__ = "brands"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        nullable=False,
        server_default=text("true"),
    )


class Category(Base, TimestampMixin):
    __tablename__ = "categories"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(
        nullable=False,
        server_default=text("true"),
    )

    __table_args__ = (
        UniqueConstraint("parent_id", "slug", name="uq_categories_parent_slug"),
        Index("ix_categories_parent_id", "parent_id"),
    )


class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    short_description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(String(8192), nullable=True)
    brand_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brands.id", ondelete="SET NULL"),
        nullable=True,
    )
    category_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Sellers phase 2 — owning seller. Backfilled by migration 0034
    # to the Hypershop Direct seed row for first-party catalog. Stays
    # nullable for one release after migration 0034 so a soft cleanup
    # window exists; a follow-up migration can flip to NOT NULL once
    # the operator confirms no rows leak through.
    seller_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[ProductStatus] = mapped_column(
        String(16),
        nullable=False,
        server_default=ProductStatus.DRAFT.value,
    )
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    tax_class: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="standard",
    )
    attributes: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    search_text: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Mother SKU — application-generated, immutable after create. Variant SKUs
    # default to "{mother_sku}-V{nn}" but callers may override.
    mother_sku: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)

    # Pharma fields. Enforced via CHECK constraint when is_medicine=true.
    is_medicine: Mapped[bool] = mapped_column(nullable=False)
    requires_prescription: Mapped[bool] = mapped_column(nullable=False)
    generic_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    strength: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dosage_form: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Visibility gates on top of status. Either expiry-passed or admin-blocked
    # hides a product from public surfaces regardless of status.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    variants: Mapped[list[ProductVariant]] = relationship(
        "ProductVariant",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductVariant.sort_order",
        lazy="selectin",
    )
    media: Mapped[list[ProductMedia]] = relationship(
        "ProductMedia",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductMedia.position",
        lazy="selectin",
    )
    brand: Mapped[Brand | None] = relationship("Brand", lazy="joined")
    category: Mapped[Category | None] = relationship("Category", lazy="joined")

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','active','archived')",
            name="status_enum",
        ),
        CheckConstraint(
            "base_currency = upper(base_currency) AND length(base_currency) = 3",
            name="base_currency_iso",
        ),
        CheckConstraint(
            (
                "is_medicine = false OR ("
                "generic_name IS NOT NULL AND generic_name <> '' AND "
                "strength IS NOT NULL AND strength <> '' AND "
                "brand_id IS NOT NULL"
                ")"
            ),
            name="medicine_required_fields",
        ),
        CheckConstraint(
            "(blocked_at IS NULL) = (blocked_reason IS NULL)",
            name="block_pair",
        ),
        Index("ix_products_status", "status"),
        Index("ix_products_brand_id", "brand_id"),
        Index("ix_products_category_id", "category_id"),
        Index("ix_products_blocked_at", "blocked_at"),
        Index("ix_products_expires_at", "expires_at"),
    )


class ProductVariant(Base, TimestampMixin):
    __tablename__ = "product_variants"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    options: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    compare_at_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    weight_grams: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(
        nullable=False,
        server_default=text("true"),
    )

    product: Mapped[Product] = relationship("Product", back_populates="variants")

    __table_args__ = (
        CheckConstraint("price >= 0", name="price_nonneg"),
        CheckConstraint(
            "compare_at_price IS NULL OR compare_at_price >= price",
            name="compare_at_ge_price",
        ),
        CheckConstraint("weight_grams IS NULL OR weight_grams >= 0", name="weight_nonneg"),
        Index("ix_product_variants_product_id", "product_id"),
    )


class ProductMedia(Base, TimestampMixin):
    __tablename__ = "product_media"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    variant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="CASCADE"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="image")
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    alt: Mapped[str | None] = mapped_column(String(255), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    product: Mapped[Product] = relationship("Product", back_populates="media")

    __table_args__ = (
        CheckConstraint("kind IN ('image','video')", name="kind_enum"),
        Index("ix_product_media_product_id", "product_id"),
    )
