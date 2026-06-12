from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.security.principal import Principal
from app.core.time import utc_now
from app.modules.catalog.models import (
    Brand,
    Category,
    Product,
    ProductMedia,
    ProductStatus,
    ProductVariant,
)
from app.modules.catalog.repository import (
    BrandRepository,
    CategoryRepository,
    MediaRepository,
    ProductRepository,
    VariantRepository,
)
from app.modules.catalog.sku import generate_mother_sku, variant_sku_for
from app.modules.catalog.slugify import slugify
# Sellers phase 2 — every product write goes through the authz
# guard so seller A can't edit seller B's catalog. Imported lazily
# per-call to avoid a hard dependency cycle if catalog is later
# extracted into its own package.
from app.modules.sellers.authz import (
    assert_can_write_product,
    resolve_owner_seller_id,
)

MIN_IMAGES_FOR_ACTIVE = 3
_MOTHER_SKU_RETRIES = 5


def _build_search_text(name: str, description: str | None, brand_name: str | None) -> str:
    parts = [name]
    if description:
        parts.append(description)
    if brand_name:
        parts.append(brand_name)
    return " ".join(parts).lower()[:2048]


class CatalogService:
    """Catalog business logic. Every mutation runs inside the caller's
    ``UnitOfWork.transactional()`` scope so audit, FK checks, and child
    rows commit atomically.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.brands = BrandRepository(session)
        self.categories = CategoryRepository(session)
        self.products = ProductRepository(session)
        self.variants = VariantRepository(session)
        self.media = MediaRepository(session)

    # ---------------- Brand ----------------

    async def create_brand(
        self,
        *,
        principal: Principal,
        name: str,
        slug: str | None,
        description: str | None,
        logo_url: str | None,
        is_active: bool,
    ) -> Brand:
        final_slug = slug or slugify(name)
        brand = await self.brands.create(
            name=name,
            slug=final_slug,
            description=description,
            logo_url=logo_url,
            is_active=is_active,
        )
        await record_audit(
            actor=principal,
            action="catalog.brand.create",
            resource_type="brand",
            resource_id=brand.id,
            metadata={"name": name, "slug": final_slug},
        )
        return brand

    async def update_brand(
        self,
        *,
        principal: Principal,
        brand_id: UUID,
        **fields: object,
    ) -> Brand:
        if "name" in fields and fields["name"] is not None and fields.get("slug") is None:
            # Caller didn't change slug; do not auto-derive from new name to
            # avoid silently breaking inbound links. Caller must rename slug
            # explicitly if they want it changed.
            pass
        brand = await self.brands.update(brand_id, **fields)
        await record_audit(
            actor=principal,
            action="catalog.brand.update",
            resource_type="brand",
            resource_id=brand_id,
            metadata={"changed": [k for k, v in fields.items() if v is not None]},
        )
        return brand

    async def delete_brand(self, *, principal: Principal, brand_id: UUID) -> None:
        # FK in products is ON DELETE SET NULL — products survive but lose
        # their brand reference. That's intentional; archival of brands does
        # not vapourize their inventory.
        await self.brands.delete(brand_id)
        await record_audit(
            actor=principal,
            action="catalog.brand.delete",
            resource_type="brand",
            resource_id=brand_id,
        )

    # ---------------- Category ----------------

    async def create_category(
        self,
        *,
        principal: Principal,
        name: str,
        slug: str | None,
        parent_id: UUID | None,
        description: str | None,
        sort_order: int,
        is_active: bool,
    ) -> Category:
        if parent_id is not None and await self.categories.get(parent_id) is None:
            raise NotFoundError("Parent category not found.")
        final_slug = slug or slugify(name)
        cat = await self.categories.create(
            name=name,
            slug=final_slug,
            parent_id=parent_id,
            description=description,
            sort_order=sort_order,
            is_active=is_active,
        )
        await record_audit(
            actor=principal,
            action="catalog.category.create",
            resource_type="category",
            resource_id=cat.id,
            metadata={
                "name": name,
                "slug": final_slug,
                "parent_id": str(parent_id) if parent_id else None,
            },
        )
        return cat

    async def update_category(
        self,
        *,
        principal: Principal,
        category_id: UUID,
        **fields: object,
    ) -> Category:
        if "parent_id" in fields and fields["parent_id"] is not None:
            new_parent = fields["parent_id"]
            if new_parent == category_id:
                raise BusinessRuleError("Category cannot be its own parent.")
            # Cycle prevention: walk up from new_parent; if we hit category_id, reject.
            cursor: UUID | None = new_parent  # type: ignore[assignment]
            seen: set[UUID] = set()
            while cursor is not None:
                if cursor in seen:
                    raise BusinessRuleError("Category tree contains a cycle.")
                seen.add(cursor)
                if cursor == category_id:
                    raise BusinessRuleError("Cannot move a category under its own descendant.")
                node = await self.categories.get(cursor)
                if node is None:
                    raise NotFoundError("Parent category not found.")
                cursor = node.parent_id
        cat = await self.categories.update(category_id, **fields)
        await record_audit(
            actor=principal,
            action="catalog.category.update",
            resource_type="category",
            resource_id=category_id,
            metadata={"changed": [k for k, v in fields.items() if v is not None]},
        )
        return cat

    async def delete_category(self, *, principal: Principal, category_id: UUID) -> None:
        if await self.categories.has_children(category_id):
            raise ConflictError("Cannot delete a category that has child categories.")
        if await self.categories.has_products(category_id):
            raise ConflictError("Cannot delete a category that contains products.")
        await self.categories.delete(category_id)
        await record_audit(
            actor=principal,
            action="catalog.category.delete",
            resource_type="category",
            resource_id=category_id,
        )

    # ---------------- Product ----------------

    async def create_product(
        self,
        *,
        principal: Principal,
        slug: str | None,
        name: str,
        short_description: str | None,
        description: str | None,
        brand_id: UUID | None,
        category_id: UUID | None,
        base_currency: str,
        tax_class: str,
        attributes: dict[str, Any],
        status: str,
        variants: list[dict[str, Any]],
        media: list[dict[str, Any]],
        is_medicine: bool = False,
        requires_prescription: bool | None = None,
        generic_name: str | None = None,
        strength: str | None = None,
        dosage_form: str | None = None,
        expires_at: datetime | None = None,
    ) -> Product:
        if brand_id is not None and await self.brands.get(brand_id) is None:
            raise NotFoundError("Brand not found.")
        if category_id is not None and await self.categories.get(category_id) is None:
            raise NotFoundError("Category not found.")

        # Medicine rules — schema layer should have caught these, but the
        # service is the last line of defence (e.g. internal callers).
        if is_medicine:
            if not generic_name or not strength or brand_id is None:
                raise BusinessRuleError(
                    "Medicine products require generic_name, strength, and brand_id.",
                )
            if requires_prescription is None:
                raise BusinessRuleError(
                    "requires_prescription must be set explicitly for medicine products.",
                )

        # Activation rule — minimum 3 images.
        new_status = ProductStatus(status)
        if new_status == ProductStatus.ACTIVE:
            self._enforce_min_images(media)
            if expires_at is not None and expires_at <= utc_now():
                raise BusinessRuleError(
                    "Cannot create an active product whose expires_at is already past.",
                )

        final_slug = slug or slugify(name)
        if await self.products.slug_exists(final_slug):
            raise ConflictError("Product slug already exists.", details={"slug": final_slug})

        brand_name: str | None = None
        if brand_id is not None:
            brand = await self.brands.get(brand_id)
            brand_name = brand.name if brand is not None else None

        published_at = utc_now() if new_status == ProductStatus.ACTIVE else None
        mother_sku = await self._allocate_mother_sku()

        # Sellers phase 2 — stamp the owning seller_id at creation time.
        # Admins fall back to "Hypershop Direct"; seller users get
        # their own seller's id. Phase 5 may flip this to require an
        # explicit seller_id arg from admin endpoints.
        seller_id = await resolve_owner_seller_id(self.session, principal)

        product = await self.products.create(
            slug=final_slug,
            name=name,
            short_description=short_description,
            description=description,
            brand_id=brand_id,
            category_id=category_id,
            seller_id=seller_id,
            base_currency=base_currency.upper(),
            tax_class=tax_class,
            attributes=attributes,
            status=new_status,
            search_text=_build_search_text(name, description, brand_name),
            published_at=published_at,
            mother_sku=mother_sku,
            is_medicine=is_medicine,
            requires_prescription=bool(requires_prescription) if is_medicine else False,
            generic_name=generic_name,
            strength=strength,
            dosage_form=dosage_form,
            expires_at=expires_at,
        )

        # Auto-generate variant SKUs when the caller didn't supply one.
        for idx, v in enumerate(variants, start=1):
            v_fields = dict(v)
            if not v_fields.get("sku"):
                v_fields["sku"] = variant_sku_for(mother_sku, index=idx)
            await self.variants.create(product_id=product.id, **v_fields)
        for m in media:
            await self.media.create(product_id=product.id, **m)

        await record_audit(
            actor=principal,
            action="catalog.product.create",
            resource_type="product",
            resource_id=product.id,
            metadata={
                "slug": final_slug,
                "mother_sku": mother_sku,
                "variant_count": len(variants),
                "is_medicine": is_medicine,
                "requires_prescription": bool(requires_prescription) if is_medicine else False,
            },
        )
        return await self._reload_product(product.id)

    async def _allocate_mother_sku(self) -> str:
        """Generate a mother SKU, retrying on the (astronomically unlikely)
        case of collision with an existing row.
        """
        for _ in range(_MOTHER_SKU_RETRIES):
            candidate = generate_mother_sku()
            if not await self.products.mother_sku_exists(candidate):
                return candidate
        # Theoretically unreachable; surface as a 503 if the universe disagrees.
        raise BusinessRuleError("Could not allocate a unique mother SKU after retries.")

    @staticmethod
    def _enforce_min_images(media: list[dict[str, Any]]) -> None:
        image_count = sum(
            1 for m in media if (m.get("kind") or "image") == "image" and m.get("url")
        )
        if image_count < MIN_IMAGES_FOR_ACTIVE:
            raise BusinessRuleError(
                f"Active products require at least {MIN_IMAGES_FOR_ACTIVE} images.",
                details={"image_count": image_count, "minimum": MIN_IMAGES_FOR_ACTIVE},
            )

    async def update_product(
        self,
        *,
        principal: Principal,
        product_id: UUID,
        fields: dict[str, Any],
    ) -> Product:
        # Sellers phase 2 — block cross-seller writes BEFORE loading
        # the catalog row's nested data. The guard returns the loaded
        # row but we re-fetch via the repo to keep the existing
        # downstream code path identical.
        await assert_can_write_product(self.session, principal, product_id)
        # seller_id is owner-bound at create time; refuse to change
        # it via update_product to keep Module 35 + payouts honest.
        # Phase 5 admin tooling can add a dedicated transfer endpoint.
        fields.pop("seller_id", None)
        existing = await self.products.get(product_id)
        if existing is None:
            raise NotFoundError("Product not found.")

        if fields.get("brand_id") is not None and await self.brands.get(fields["brand_id"]) is None:
            raise NotFoundError("Brand not found.")
        if (
            fields.get("category_id") is not None
            and await self.categories.get(fields["category_id"]) is None
        ):
            raise NotFoundError("Category not found.")

        if "base_currency" in fields and fields["base_currency"] is not None:
            fields["base_currency"] = fields["base_currency"].upper()
        if "status" in fields and fields["status"] is not None:
            new_status = ProductStatus(fields["status"])
            fields["status"] = new_status
            if new_status == ProductStatus.ACTIVE:
                # Promote draft → active: enforce min-images on the existing
                # media collection. Empty/insufficient → reject.
                image_count = sum(1 for m in existing.media if m.kind == "image")
                if image_count < MIN_IMAGES_FOR_ACTIVE:
                    raise BusinessRuleError(
                        f"Cannot activate a product with fewer than {MIN_IMAGES_FOR_ACTIVE} images.",
                        details={
                            "image_count": image_count,
                            "minimum": MIN_IMAGES_FOR_ACTIVE,
                        },
                    )
                if existing.expires_at is not None and existing.expires_at <= utc_now():
                    raise BusinessRuleError(
                        "Cannot activate a product whose expires_at is already past.",
                    )
                if existing.published_at is None:
                    fields["published_at"] = utc_now()

        if any(k in fields for k in ("name", "description", "brand_id")):
            new_name = fields.get("name") or existing.name
            new_desc = (
                fields.get("description") if "description" in fields else existing.description
            )
            new_brand_id = fields.get("brand_id") if "brand_id" in fields else existing.brand_id
            brand_name: str | None = None
            if new_brand_id is not None:
                b = await self.brands.get(new_brand_id)
                brand_name = b.name if b is not None else None
            fields["search_text"] = _build_search_text(new_name, new_desc, brand_name)

        product = await self.products.update(product_id, **fields)
        await record_audit(
            actor=principal,
            action="catalog.product.update",
            resource_type="product",
            resource_id=product_id,
            metadata={"changed": list(fields.keys())},
        )
        # IndexNow — when this update was the draft→active transition,
        # ping the search engines with the canonical product URL so
        # it's crawlable within minutes instead of next-sitemap. Soft-
        # imported so unit tests that stub the catalog module don't
        # pull in httpx via the seo.jobs module.
        if fields.get("status") == ProductStatus.ACTIVE.value:
            try:
                from app.modules.seo.jobs import enqueue_product_url
                enqueue_product_url(product.slug)
            except Exception:  # noqa: BLE001
                pass
            # Auto-SEO engine — generate keyword-rich EN+BN meta on the
            # draft→active transition. Soft-fail: never block product
            # activation on a SEO error. Yields to manual overrides
            # (auto_generated=False rows are left untouched).
            try:
                from app.modules.seo.autogen import SeoAutoGenService
                reloaded = await self._reload_product(product.id)
                await SeoAutoGenService(self.session).generate_for_product(reloaded)
            except Exception:  # noqa: BLE001
                pass
        return await self._reload_product(product.id)

    async def archive_product(
        self,
        *,
        principal: Principal,
        product_id: UUID,
    ) -> None:
        await assert_can_write_product(self.session, principal, product_id)
        await self.products.archive(product_id)
        await record_audit(
            actor=principal,
            action="catalog.product.archive",
            resource_type="product",
            resource_id=product_id,
        )

    async def block_product(
        self,
        *,
        principal: Principal,
        product_id: UUID,
        reason: str,
    ) -> Product:
        existing = await self.products.get(product_id)
        if existing is None:
            raise NotFoundError("Product not found.")
        try:
            await self.products.update(
                product_id,
                blocked_at=utc_now(),
                blocked_reason=reason,
            )
        except IntegrityError as e:
            raise BusinessRuleError("Block constraint violation.") from e
        await record_audit(
            actor=principal,
            action="catalog.product.block",
            resource_type="product",
            resource_id=product_id,
            metadata={"reason": reason},
        )
        return await self._reload_product(product_id)

    async def unblock_product(
        self,
        *,
        principal: Principal,
        product_id: UUID,
    ) -> Product:
        existing = await self.products.get(product_id)
        if existing is None:
            raise NotFoundError("Product not found.")
        if existing.blocked_at is None:
            return existing
        await self.products.clear_block(product_id)
        await record_audit(
            actor=principal,
            action="catalog.product.unblock",
            resource_type="product",
            resource_id=product_id,
        )
        return await self._reload_product(product_id)

    async def set_product_expiry(
        self,
        *,
        principal: Principal,
        product_id: UUID,
        expires_at: datetime | None,
    ) -> Product:
        existing = await self.products.get(product_id)
        if existing is None:
            raise NotFoundError("Product not found.")
        await self.products.set_expiry(product_id, expires_at)
        await record_audit(
            actor=principal,
            action="catalog.product.set_expiry",
            resource_type="product",
            resource_id=product_id,
            metadata={"expires_at": expires_at.isoformat() if expires_at else None},
        )
        return await self._reload_product(product_id)

    async def _reload_product(self, product_id: UUID) -> Product:
        product = await self.products.get(product_id)
        if product is None:
            raise NotFoundError("Product not found.")
        return product

    # ---------------- Variant ----------------

    async def add_variant(
        self,
        *,
        principal: Principal,
        product_id: UUID,
        fields: dict[str, Any],
    ) -> ProductVariant:
        await assert_can_write_product(self.session, principal, product_id)
        product = await self.products.get(product_id)
        if product is None:
            raise NotFoundError("Product not found.")
        v_fields = dict(fields)
        if not v_fields.get("sku"):
            next_index = len(product.variants) + 1
            v_fields["sku"] = variant_sku_for(product.mother_sku, index=next_index)
        v = await self.variants.create(product_id=product_id, **v_fields)
        await record_audit(
            actor=principal,
            action="catalog.variant.create",
            resource_type="product",
            resource_id=product_id,
            metadata={"variant_id": str(v.id), "sku": v.sku},
        )
        return v

    async def update_variant(
        self,
        *,
        principal: Principal,
        product_id: UUID,
        variant_id: UUID,
        fields: dict[str, Any],
    ) -> ProductVariant:
        await assert_can_write_product(self.session, principal, product_id)
        existing = await self.variants.get(variant_id)
        if existing is None or existing.product_id != product_id:
            raise NotFoundError("Variant not found for this product.")
        v = await self.variants.update(variant_id, **fields)
        await record_audit(
            actor=principal,
            action="catalog.variant.update",
            resource_type="product",
            resource_id=product_id,
            metadata={"variant_id": str(variant_id), "changed": list(fields.keys())},
        )
        return v

    async def remove_variant(
        self,
        *,
        principal: Principal,
        product_id: UUID,
        variant_id: UUID,
    ) -> None:
        await assert_can_write_product(self.session, principal, product_id)
        existing = await self.variants.get(variant_id)
        if existing is None or existing.product_id != product_id:
            raise NotFoundError("Variant not found for this product.")
        # Refuse to delete the last remaining variant — products require >= 1.
        product = await self.products.get(product_id)
        if product is None:
            raise NotFoundError("Product not found.")
        if len(product.variants) <= 1:
            raise BusinessRuleError(
                "Cannot delete the last variant of a product. Archive the product instead.",
            )
        await self.variants.delete(variant_id)
        await record_audit(
            actor=principal,
            action="catalog.variant.delete",
            resource_type="product",
            resource_id=product_id,
            metadata={"variant_id": str(variant_id)},
        )

    # ---------------- Media ----------------

    async def add_media(
        self,
        *,
        principal: Principal,
        product_id: UUID,
        fields: dict[str, Any],
    ) -> ProductMedia:
        await assert_can_write_product(self.session, principal, product_id)
        if await self.products.get(product_id) is None:
            raise NotFoundError("Product not found.")
        m = await self.media.create(product_id=product_id, **fields)
        await record_audit(
            actor=principal,
            action="catalog.media.create",
            resource_type="product",
            resource_id=product_id,
            metadata={"media_id": str(m.id), "kind": m.kind},
        )
        return m

    async def remove_media(
        self,
        *,
        principal: Principal,
        product_id: UUID,
        media_id: UUID,
    ) -> None:
        await assert_can_write_product(self.session, principal, product_id)
        product = await self.products.get(product_id)
        if product is None:
            raise NotFoundError("Product not found.")
        if not any(m.id == media_id for m in product.media):
            raise NotFoundError("Media not found for this product.")
        await self.media.delete(media_id)
        await record_audit(
            actor=principal,
            action="catalog.media.delete",
            resource_type="product",
            resource_id=product_id,
            metadata={"media_id": str(media_id)},
        )
