"""Admin write endpoints under /api/v1/admin/storefront/*.

Permission: ``storefront.configure`` (added with this module).

Every write fires `trigger_storefront_revalidate()` so the customer-
web's `revalidateTag('storefront')` flushes within seconds of save.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi import status as http_status

from app.core.audit import record_audit
from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.storefront_cms.repository import (
    FeaturedCategoryRepository,
    NavItemRepository,
    StaticPageRepository,
)
from app.modules.storefront_cms.revalidate import (
    bump_version,
    trigger_storefront_revalidate,
)
from app.modules.storefront_cms.schemas import (
    FeaturedCategoryResponse,
    FeaturedCategoryUpsert,
    NavItemResponse,
    NavItemUpsert,
    StaticPageListItem,
    StaticPageResponse,
    StaticPageUpsert,
)

router = APIRouter(prefix="/admin/storefront", tags=["admin-storefront"])

_WRITE = "storefront.configure"


async def _audit(
    actor: Principal, action: str, resource_id: str | UUID,
) -> None:
    """Audit log — must be called INSIDE the uow.transactional() block
    because record_audit() reads the active session from contextvars."""
    await record_audit(
        actor=actor,
        action=action,
        resource_type="storefront_cms",
        resource_id=resource_id,
    )


async def _revalidate() -> None:
    """Fire the FE revalidation webhook — safe to call after the
    transactional block closes (no DB session needed)."""
    await trigger_storefront_revalidate()


# ============================================================
#  Nav items
# ============================================================
@router.get("/nav", response_model=list[NavItemResponse])
async def list_nav(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_WRITE))],
) -> list[NavItemResponse]:
    async with uow.transactional() as session:
        rows = await NavItemRepository(session).list_all()
    return [NavItemResponse.model_validate(r) for r in rows]


@router.post("/nav", response_model=NavItemResponse, status_code=201)
async def create_nav(
    body: NavItemUpsert,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_WRITE))],
) -> NavItemResponse:
    async with uow.transactional() as session:
        row = await NavItemRepository(session).add(**body.model_dump())
        await _audit(principal, "storefront.nav.create", row.id)
    await _revalidate()
    return NavItemResponse.model_validate(row)


@router.patch("/nav/{nav_id}", response_model=NavItemResponse)
async def update_nav(
    nav_id: UUID,
    body: NavItemUpsert,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_WRITE))],
) -> NavItemResponse:
    async with uow.transactional() as session:
        repo = NavItemRepository(session)
        n = await repo.update(nav_id, **body.model_dump())
        if n == 0:
            raise HTTPException(status_code=404, detail="Nav item not found")
        row = await repo.get(nav_id)
        await _audit(principal, "storefront.nav.update", nav_id)
    await _revalidate()
    return NavItemResponse.model_validate(row)


@router.delete(
    "/nav/{nav_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
async def delete_nav(
    nav_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_WRITE))],
) -> Response:
    async with uow.transactional() as session:
        n = await NavItemRepository(session).delete(nav_id)
        if n == 0:
            raise HTTPException(status_code=404, detail="Nav item not found")
        await _audit(principal, "storefront.nav.delete", nav_id)
    await _revalidate()
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


# ============================================================
#  Featured categories
# ============================================================
@router.get("/featured", response_model=list[FeaturedCategoryResponse])
async def list_featured(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_WRITE))],
) -> list[FeaturedCategoryResponse]:
    async with uow.transactional() as session:
        rows = await FeaturedCategoryRepository(session).list_all()
    return [FeaturedCategoryResponse.model_validate(r) for r in rows]


@router.post("/featured", response_model=FeaturedCategoryResponse, status_code=201)
async def create_featured(
    body: FeaturedCategoryUpsert,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_WRITE))],
) -> FeaturedCategoryResponse:
    async with uow.transactional() as session:
        row = await FeaturedCategoryRepository(session).add(**body.model_dump())
        await _audit(principal, "storefront.featured.create", row.id)
    await _revalidate()
    return FeaturedCategoryResponse.model_validate(row)


@router.patch("/featured/{fid}", response_model=FeaturedCategoryResponse)
async def update_featured(
    fid: UUID,
    body: FeaturedCategoryUpsert,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_WRITE))],
) -> FeaturedCategoryResponse:
    async with uow.transactional() as session:
        repo = FeaturedCategoryRepository(session)
        n = await repo.update(fid, **body.model_dump())
        if n == 0:
            raise HTTPException(status_code=404, detail="Featured category not found")
        row = await repo.get(fid)
        await _audit(principal, "storefront.featured.update", fid)
    await _revalidate()
    return FeaturedCategoryResponse.model_validate(row)


@router.delete(
    "/featured/{fid}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
async def delete_featured(
    fid: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_WRITE))],
) -> Response:
    async with uow.transactional() as session:
        n = await FeaturedCategoryRepository(session).delete(fid)
        if n == 0:
            raise HTTPException(status_code=404, detail="Featured category not found")
        await _audit(principal, "storefront.featured.delete", fid)
    await _revalidate()
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


# ============================================================
#  Static pages
# ============================================================
@router.get("/pages", response_model=list[StaticPageListItem])
async def list_pages(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_WRITE))],
) -> list[StaticPageListItem]:
    async with uow.transactional() as session:
        rows = await StaticPageRepository(session).list_all()
    return [StaticPageListItem.model_validate(r) for r in rows]


@router.get("/pages/{pid}", response_model=StaticPageResponse)
async def get_page(
    pid: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_WRITE))],
) -> StaticPageResponse:
    async with uow.transactional() as session:
        row = await StaticPageRepository(session).get(pid)
    if row is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return StaticPageResponse.model_validate(row)


@router.post("/pages", response_model=StaticPageResponse, status_code=201)
async def create_page(
    body: StaticPageUpsert,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_WRITE))],
) -> StaticPageResponse:
    async with uow.transactional() as session:
        repo = StaticPageRepository(session)
        # Slug collision = 409 (admin can pick a different slug or
        # edit the existing row via PATCH).
        existing = await repo.get_by_slug(body.slug)
        if existing is not None:
            raise HTTPException(status_code=409, detail="Slug already exists")
        row = await repo.add(**body.model_dump())
        await _audit(principal, "storefront.page.create", row.id)
    await _revalidate()
    return StaticPageResponse.model_validate(row)


@router.patch("/pages/{pid}", response_model=StaticPageResponse)
async def update_page(
    pid: UUID,
    body: StaticPageUpsert,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_WRITE))],
) -> StaticPageResponse:
    async with uow.transactional() as session:
        repo = StaticPageRepository(session)
        n = await repo.update(pid, **body.model_dump())
        if n == 0:
            raise HTTPException(status_code=404, detail="Page not found")
        row = await repo.get(pid)
        await _audit(principal, "storefront.page.update", pid)
    await _revalidate()
    return StaticPageResponse.model_validate(row)


@router.delete(
    "/pages/{pid}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
async def delete_page(
    pid: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_WRITE))],
) -> Response:
    async with uow.transactional() as session:
        n = await StaticPageRepository(session).delete(pid)
        if n == 0:
            raise HTTPException(status_code=404, detail="Page not found")
        await _audit(principal, "storefront.page.delete", pid)
    await _revalidate()
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


# ============================================================
#  Manual revalidation trigger
# ============================================================
@router.post("/publish", status_code=202)
async def publish_now(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_WRITE))],
) -> dict[str, int]:
    """Manually fire the revalidation webhook without making a write.

    Useful when the storefront cache is misbehaving and the admin
    wants a fresh fetch without editing anything. Returns the new
    version counter so the admin UI can confirm the push landed.
    """
    async with uow.transactional():
        await _audit(principal, "storefront.publish.manual", "*")
    bump_version()
    await trigger_storefront_revalidate()
    from app.modules.storefront_cms.revalidate import current_version
    return {"version": current_version()}
