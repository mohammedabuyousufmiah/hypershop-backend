from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.delivery.repository import DeliveryZoneRepository
from app.modules.delivery.schemas import (
    DeliveryZoneCreate,
    DeliveryZoneResponse,
    DeliveryZoneUpdate,
)
from app.modules.delivery.service import DeliveryService

router = APIRouter(prefix="/admin/delivery", tags=["admin-delivery"])


# Dedicated delivery-zone perms (split from catalog.product.write
# 2026-05-16, later session). The original `catalog.product.read` gate
# leaked internal zone config to every browsing customer; the temporary
# tightening to `catalog.product.write` over-restricted read access for
# oversight roles. Now properly split:
#   - `delivery.zone.read`  → admin, manager, supervisor, dispatcher,
#                              rider_manager. Visibility for SLA + audit
#                              + dispatch ETA decisions.
#   - `delivery.zone.write` → admin, manager only. Rate negotiation +
#                              new-zone provisioning is merchant ops.
_RW = "delivery.zone.write"
_READ = "delivery.zone.read"


@router.get(
    "/zones",
    response_model=list[DeliveryZoneResponse],
    dependencies=[Depends(requires_permission(_READ))],
)
async def admin_list_zones(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> list[DeliveryZoneResponse]:
    async with uow.transactional() as session:
        repo = DeliveryZoneRepository(session)
        rows = await repo.list_all()
        return [DeliveryZoneResponse.model_validate(z) for z in rows]


@router.post(
    "/zones",
    response_model=DeliveryZoneResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_RW))],
)
async def admin_create_zone(
    payload: DeliveryZoneCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DeliveryZoneResponse:
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        zone = await svc.create_zone(principal=principal, **payload.model_dump())
        return DeliveryZoneResponse.model_validate(zone)


@router.patch(
    "/zones/{zone_id}",
    response_model=DeliveryZoneResponse,
    dependencies=[Depends(requires_permission(_RW))],
)
async def admin_update_zone(
    zone_id: UUID,
    payload: DeliveryZoneUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DeliveryZoneResponse:
    fields = payload.model_dump(exclude_unset=True)
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        zone = await svc.update_zone(principal=principal, zone_id=zone_id, **fields)
        return DeliveryZoneResponse.model_validate(zone)


@router.delete(
    "/zones/{zone_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_RW))],
)
async def admin_delete_zone(
    zone_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        await svc.delete_zone(principal=principal, zone_id=zone_id)
