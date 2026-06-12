"""Admin-side courier endpoints — provider toggles, credential CRUD,
shipment list / refresh / cancel."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import IntegrationError
from app.core.security.rbac import requires_permission
from app.modules.couriers import repository as repo
from app.modules.couriers import service as courier_service
from app.modules.couriers.codes import ALL_PROVIDERS
from app.modules.couriers.schemas import (
    CourierCredentialCreate,
    CourierCredentialRead,
    CourierCredentialUpdate,
    CourierProviderRead,
    CourierProviderUpdate,
    CourierShipmentRead,
    ShipmentCancelRequest,
)

router = APIRouter(prefix="/admin/couriers", tags=["admin-couriers"])

_PERM_VIEW = "couriers.view"
_PERM_MANAGE = "couriers.manage"


# ─── Providers ──────────────────────────────────────────────────────


@router.get(
    "/providers",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="List all seeded courier providers + enabled state",
)
async def list_providers(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        rows = await repo.list_providers(session)
    return {
        "items": [CourierProviderRead.model_validate(r).model_dump(mode="json")
                  for r in rows],
        "total": len(rows),
    }


@router.patch(
    "/providers/{code}",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Enable / disable a courier provider",
)
async def toggle_provider(
    code: str,
    body: CourierProviderUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    if code not in ALL_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown courier code.")
    async with uow.transactional() as session:
        try:
            row = await repo.enable_provider(session, code, body.is_enabled)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    return CourierProviderRead.model_validate(row).model_dump(mode="json")


# ─── Credentials ────────────────────────────────────────────────────


@router.get(
    "/providers/{code}/credentials",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="List credentials for a courier (api_key / api_secret masked)",
)
async def list_credentials(
    code: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    if code not in ALL_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown courier code.")
    async with uow.transactional() as session:
        rows = await repo.list_credentials(session, code)
    return {
        "items": [
            CourierCredentialRead.from_orm_masked(r).model_dump(mode="json")
            for r in rows
        ],
        "total": len(rows),
    }


@router.post(
    "/providers/{code}/credentials",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Add a credential row for a courier",
    status_code=201,
)
async def create_credential(
    code: str,
    body: CourierCredentialCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    if code not in ALL_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown courier code.")
    async with uow.transactional() as session:
        row = await repo.create_credential(
            session,
            provider_code=code,
            environment=body.environment,
            base_url=body.base_url,
            api_key=body.api_key,
            api_secret=body.api_secret,
            client_id=body.client_id,
            merchant_id=body.merchant_id,
            extra_config=body.extra_config,
            is_active=body.is_active,
        )
    return CourierCredentialRead.from_orm_masked(row).model_dump(mode="json")


@router.patch(
    "/credentials/{cred_id}",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Update a credential row (partial)",
)
async def update_credential(
    cred_id: UUID,
    body: CourierCredentialUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    fields = body.model_dump(exclude_unset=True)
    async with uow.transactional() as session:
        row = await repo.update_credential(session, cred_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail="Credential not found.")
    return CourierCredentialRead.from_orm_masked(row).model_dump(mode="json")


@router.delete(
    "/credentials/{cred_id}",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Soft-delete a credential (sets is_active=false)",
)
async def delete_credential(
    cred_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        row = await repo.update_credential(session, cred_id, is_active=False)
    if row is None:
        raise HTTPException(status_code=404, detail="Credential not found.")
    return {"id": str(cred_id), "is_active": False}


# ─── Shipments ──────────────────────────────────────────────────────


@router.get(
    "/shipments",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="List shipments with optional status / provider filter",
)
async def list_shipments(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    provider_code: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        rows, total = await repo.list_shipments(
            session,
            status=status_filter,
            provider_code=provider_code,
            limit=limit,
            offset=offset,
        )
    return {
        "items": [
            CourierShipmentRead.model_validate(r).model_dump(mode="json")
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/shipments/{shipment_id}",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Read a single shipment",
)
async def get_shipment(
    shipment_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        row = await repo.get_shipment(session, shipment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Shipment not found.")
    return CourierShipmentRead.model_validate(row).model_dump(mode="json")


@router.post(
    "/shipments/{shipment_id}/refresh-status",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Pull current status from the courier + persist the event",
)
async def refresh_shipment_status(
    shipment_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    try:
        async with uow.transactional() as session:
            return await courier_service.refresh_status(session, shipment_id)
    except courier_service.ShipmentNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except IntegrationError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post(
    "/shipments/{shipment_id}/cancel",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Cancel a shipment with the courier (when allowed)",
)
async def cancel_shipment(
    shipment_id: UUID,
    body: ShipmentCancelRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    try:
        async with uow.transactional() as session:
            return await courier_service.cancel_shipment(
                session, shipment_id, body.reason,
            )
    except courier_service.ShipmentNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except IntegrationError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
