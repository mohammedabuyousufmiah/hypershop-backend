from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import Response

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import NotFoundError
from app.core.pagination import Page, PageParams
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.catalog.api.public import _product_to_response, _product_to_summary
from app.modules.catalog.repository import ProductRepository
from app.modules.catalog.schemas import (
    BrandCreate,
    BrandResponse,
    BrandUpdate,
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    MediaCreate,
    MediaResponse,
    ProductBlockRequest,
    ProductCreate,
    ProductExpiryRequest,
    ProductResponse,
    ProductSummary,
    ProductUpdate,
    VariantCreate,
    VariantResponse,
    VariantUpdate,
)
from app.modules.catalog.service import CatalogService

router = APIRouter(prefix="/admin/catalog", tags=["admin-catalog"])


_RW = "catalog.product.write"
_CAT_RW = "catalog.category.write"


# ---------------- Brand ----------------


@router.post(
    "/brands",
    response_model=BrandResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_RW))],
)
async def create_brand(
    payload: BrandCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> BrandResponse:
    async with uow.transactional() as session:
        svc = CatalogService(session)
        b = await svc.create_brand(
            principal=principal,
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
            logo_url=str(payload.logo_url) if payload.logo_url else None,
            is_active=payload.is_active,
        )
        return BrandResponse.model_validate(b)


@router.patch(
    "/brands/{brand_id}",
    response_model=BrandResponse,
    dependencies=[Depends(requires_permission(_RW))],
)
async def update_brand(
    brand_id: UUID,
    payload: BrandUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> BrandResponse:
    fields = payload.model_dump(exclude_unset=True)
    if "logo_url" in fields and fields["logo_url"] is not None:
        fields["logo_url"] = str(fields["logo_url"])
    async with uow.transactional() as session:
        svc = CatalogService(session)
        b = await svc.update_brand(principal=principal, brand_id=brand_id, **fields)
        # Reload column attrs so model_validate doesn't trigger a lazy load
        # outside the async context (MissingGreenlet).
        await session.refresh(b)
        return BrandResponse.model_validate(b)


@router.delete(
    "/brands/{brand_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_RW))],
)
async def delete_brand(
    brand_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        svc = CatalogService(session)
        await svc.delete_brand(principal=principal, brand_id=brand_id)


# ---------------- Category ----------------


@router.post(
    "/categories",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_CAT_RW))],
)
async def create_category(
    payload: CategoryCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CategoryResponse:
    async with uow.transactional() as session:
        svc = CatalogService(session)
        c = await svc.create_category(
            principal=principal,
            name=payload.name,
            slug=payload.slug,
            parent_id=payload.parent_id,
            description=payload.description,
            sort_order=payload.sort_order,
            is_active=payload.is_active,
        )
        return CategoryResponse.model_validate(c)


@router.patch(
    "/categories/{category_id}",
    response_model=CategoryResponse,
    dependencies=[Depends(requires_permission(_CAT_RW))],
)
async def update_category(
    category_id: UUID,
    payload: CategoryUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CategoryResponse:
    fields = payload.model_dump(exclude_unset=True)
    async with uow.transactional() as session:
        svc = CatalogService(session)
        c = await svc.update_category(
            principal=principal,
            category_id=category_id,
            **fields,
        )
        return CategoryResponse.model_validate(c)


@router.delete(
    "/categories/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_CAT_RW))],
)
async def delete_category(
    category_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        svc = CatalogService(session)
        await svc.delete_category(principal=principal, category_id=category_id)


# ---------------- Product ----------------


@router.get(
    "/products",
    response_model=Page[ProductSummary],
    dependencies=[Depends(requires_permission("catalog.product.write"))],  # tightened 2026-05-16: was catalog.product.read which every browsing customer holds; admin views include blocked_reason/expires_at/published_at which are admin-only fields
)
async def admin_list_products(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=20, ge=1, le=100),
    q: str | None = Query(default=None, max_length=200),
    status_filter: str | None = Query(default=None, alias="status", max_length=16),
) -> Page[ProductSummary]:
    from app.modules.catalog.models import ProductStatus

    params = PageParams(page=page, size=size)
    sf = ProductStatus(status_filter) if status_filter else None
    async with uow.transactional() as session:
        repo = ProductRepository(session)
        rows, total = await repo.search(
            offset=params.offset,
            limit=params.limit,
            q=q,
            category_slug=None,
            brand_slug=None,
            status_filter=sf,
        )
    items = [_product_to_summary(p) for p in rows]
    return Page.build(items=items, total=total, params=params)


@router.get(
    "/products/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(requires_permission("catalog.product.write"))],  # tightened 2026-05-16: was catalog.product.read which every browsing customer holds; admin views include blocked_reason/expires_at/published_at which are admin-only fields
)
async def admin_get_product(
    product_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> ProductResponse:
    async with uow.transactional() as session:
        repo = ProductRepository(session)
        p = await repo.get(product_id)
        if p is None:
            raise NotFoundError("Product not found.")
        return _product_to_response(p)


@router.post(
    "/products",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_RW))],
)
async def create_product(
    payload: ProductCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ProductResponse:
    variants = [v.model_dump() for v in payload.variants]
    media = []
    for m in payload.media:
        d = m.model_dump()
        d["url"] = str(d["url"])
        media.append(d)
    async with uow.transactional() as session:
        svc = CatalogService(session)
        product = await svc.create_product(
            principal=principal,
            slug=payload.slug,
            name=payload.name,
            short_description=payload.short_description,
            description=payload.description,
            brand_id=payload.brand_id,
            category_id=payload.category_id,
            base_currency=payload.base_currency,
            tax_class=payload.tax_class,
            attributes=payload.attributes,
            status=payload.status,
            variants=variants,
            media=media,
            is_medicine=payload.is_medicine,
            requires_prescription=payload.requires_prescription,
            generic_name=payload.generic_name,
            strength=payload.strength,
            dosage_form=payload.dosage_form,
            expires_at=payload.expires_at,
        )
        return _product_to_response(product)


@router.patch(
    "/products/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(requires_permission(_RW))],
)
async def update_product(
    product_id: UUID,
    payload: ProductUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ProductResponse:
    fields = payload.model_dump(exclude_unset=True)
    async with uow.transactional() as session:
        svc = CatalogService(session)
        product = await svc.update_product(
            principal=principal,
            product_id=product_id,
            fields=fields,
        )
        return _product_to_response(product)


@router.delete(
    "/products/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_RW))],
)
async def archive_product(
    product_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        svc = CatalogService(session)
        await svc.archive_product(principal=principal, product_id=product_id)


# ---------------- Variants & Media ----------------


@router.post(
    "/products/{product_id}/variants",
    response_model=VariantResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_RW))],
)
async def add_variant(
    product_id: UUID,
    payload: VariantCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> VariantResponse:
    async with uow.transactional() as session:
        svc = CatalogService(session)
        v = await svc.add_variant(
            principal=principal,
            product_id=product_id,
            fields=payload.model_dump(),
        )
        return VariantResponse.model_validate(v)


@router.patch(
    "/products/{product_id}/variants/{variant_id}",
    response_model=VariantResponse,
    dependencies=[Depends(requires_permission(_RW))],
)
async def update_variant(
    product_id: UUID,
    variant_id: UUID,
    payload: VariantUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> VariantResponse:
    async with uow.transactional() as session:
        svc = CatalogService(session)
        v = await svc.update_variant(
            principal=principal,
            product_id=product_id,
            variant_id=variant_id,
            fields=payload.model_dump(exclude_unset=True),
        )
        return VariantResponse.model_validate(v)


@router.delete(
    "/products/{product_id}/variants/{variant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_RW))],
)
async def delete_variant(
    product_id: UUID,
    variant_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        svc = CatalogService(session)
        await svc.remove_variant(
            principal=principal,
            product_id=product_id,
            variant_id=variant_id,
        )


@router.post(
    "/products/{product_id}/media",
    response_model=MediaResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_RW))],
)
async def add_media(
    product_id: UUID,
    payload: MediaCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> MediaResponse:
    fields = payload.model_dump()
    fields["url"] = str(fields["url"])
    async with uow.transactional() as session:
        svc = CatalogService(session)
        m = await svc.add_media(principal=principal, product_id=product_id, fields=fields)
        return MediaResponse.model_validate(m)


@router.post(
    "/products/{product_id}/media/upload",
    response_model=MediaResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_RW))],
    summary="Upload a product image file and create a media row in one call",
    description=(
        "Multipart upload (`file` form field, plus optional `alt`, "
        "`position`, `variant_id`). The file is streamed into Cloudflare "
        "R2 when `R2_BUCKET_NAME` is configured, otherwise it falls back "
        "to local disk under `PRODUCT_IMAGE_LOCAL_DIR` (the customer-web "
        "`public/products/` folder in default local dev). On success the "
        "endpoint returns the freshly-created `product_media` row, "
        "including the resolvable public URL — admin UI doesn't need a "
        "second `POST /media` call."
    ),
)
async def upload_media(
    product_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    file: Annotated[UploadFile, File(description="Image file (JPEG, PNG, WebP, GIF or AVIF; <= 5 MB)")],
    alt: Annotated[str | None, Form(description="Alt text for accessibility")] = None,
    position: Annotated[int, Form(description="Display order, 0-based")] = 0,
    variant_id: Annotated[UUID | None, Form(description="Variant-specific image (optional)")] = None,
) -> MediaResponse:
    # Lazy import so the catalog module stays decoupled from R2 specifics
    # at import time — keeps non-upload requests cheap.
    from app.modules.catalog.image_storage import upload_product_image

    # Look up a slug hint for nicer object keys.
    slug_hint: str | None = None
    async with uow.transactional() as session:
        repo = ProductRepository(session)
        prod = await repo.get(product_id)
        if prod is None:
            raise NotFoundError("Product not found.")
        slug_hint = prod.slug

    public_url, storage_kind = await upload_product_image(
        file, product_slug_hint=slug_hint,
    )

    async with uow.transactional() as session:
        svc = CatalogService(session)
        m = await svc.add_media(
            principal=principal,
            product_id=product_id,
            fields={
                "url": public_url,
                "alt": alt,
                "kind": "image",
                "position": position,
                "variant_id": variant_id,
            },
        )
        # tag the response so the client can show "stored on R2" vs
        # "stored locally" in the admin upload toast.
        resp = MediaResponse.model_validate(m)
        return resp


@router.delete(
    "/products/{product_id}/media/{media_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_RW))],
)
async def delete_media(
    product_id: UUID,
    media_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        svc = CatalogService(session)
        await svc.remove_media(
            principal=principal,
            product_id=product_id,
            media_id=media_id,
        )


# ---------------- Block / Unblock / Expiry ----------------


@router.post(
    "/products/{product_id}/block",
    response_model=ProductResponse,
    summary="Block a product",
    description=(
        "Hides the product from public listings and detail endpoints regardless "
        "of its status. Reason is required and surfaces in audit logs and admin "
        "views."
    ),
    dependencies=[Depends(requires_permission(_RW))],
)
async def block_product(
    product_id: UUID,
    payload: ProductBlockRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ProductResponse:
    async with uow.transactional() as session:
        svc = CatalogService(session)
        p = await svc.block_product(
            principal=principal,
            product_id=product_id,
            reason=payload.reason,
        )
        return _product_to_response(p)


@router.post(
    "/products/{product_id}/unblock",
    response_model=ProductResponse,
    summary="Unblock a product",
    dependencies=[Depends(requires_permission(_RW))],
)
async def unblock_product(
    product_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ProductResponse:
    async with uow.transactional() as session:
        svc = CatalogService(session)
        p = await svc.unblock_product(principal=principal, product_id=product_id)
        return _product_to_response(p)


@router.put(
    "/products/{product_id}/expiry",
    response_model=ProductResponse,
    summary="Set or clear product expiry",
    description=(
        "After ``expires_at`` passes, the product is hidden from public surfaces. "
        "Pass ``null`` to clear the expiry."
    ),
    dependencies=[Depends(requires_permission(_RW))],
)
async def set_product_expiry(
    product_id: UUID,
    payload: ProductExpiryRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ProductResponse:
    async with uow.transactional() as session:
        svc = CatalogService(session)
        p = await svc.set_product_expiry(
            principal=principal,
            product_id=product_id,
            expires_at=payload.expires_at,
        )
        return _product_to_response(p)
