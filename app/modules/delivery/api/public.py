from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.db.uow import UnitOfWork, get_uow
from app.modules.delivery.schemas import (
    DeliveryZoneResponse,
    QuoteRequest,
    QuoteResponse,
)
from app.modules.delivery.service import DeliveryService
from app.modules.delivery.repository import DeliveryZoneRepository

router = APIRouter(prefix="/delivery", tags=["delivery"])


@router.post(
    "/quote",
    response_model=QuoteResponse,
    summary="Quote a delivery fee for an address",
    description=(
        "Returns the matching zone's price plus any payment-method surcharge "
        "(currently 0 for COD). Falls back to the default zone if no city or "
        "postal code matches. Returns 404 if no zone matches and no default "
        "exists."
    ),
)
async def quote_delivery(
    payload: QuoteRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> QuoteResponse:
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        quote = await svc.quote(
            city=payload.address.city,
            postal_code=payload.address.postal_code,
            payment_method=payload.payment_method,
        )
        return QuoteResponse(
            zone_code=quote.zone_code,
            zone_name=quote.zone_name,
            kind=quote.kind,
            base_fee=quote.base_fee,
            cod_fee=quote.cod_fee,
            total=quote.total,
            currency=quote.currency,
        )


@router.get(
    "/zones",
    response_model=list[DeliveryZoneResponse],
    summary="List active delivery zones (public reference)",
)
async def list_zones(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> list[DeliveryZoneResponse]:
    async with uow.transactional() as session:
        repo = DeliveryZoneRepository(session)
        rows = await repo.list_active()
        return [DeliveryZoneResponse.model_validate(z) for z in rows]
