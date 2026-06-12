"""Courier webhook entry — generic /api/v1/couriers/webhooks/{provider}.

Returns 200 always (couriers retry on non-2xx). Signature failures and
internal errors are logged with the provider code but never expose
stack traces to the courier.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import IntegrationError
from app.core.logging import get_logger
from app.modules.couriers import service as courier_service
from app.modules.couriers.codes import ALL_PROVIDERS

_logger = get_logger("hypershop.couriers.webhooks")

router = APIRouter(prefix="/couriers/webhooks", tags=["couriers-webhooks"])


@router.post(
    "/{provider_code}",
    status_code=status.HTTP_200_OK,
    summary="Generic courier webhook — parse + log + update shipment",
)
async def courier_webhook(
    provider_code: str,
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> JSONResponse:
    if provider_code not in ALL_PROVIDERS:
        _logger.warning(
            "courier_webhook_unknown_provider",
            provider_code=provider_code,
        )
        return JSONResponse(
            {"received": True, "resolution": "unknown_provider"},
            status_code=200,
        )

    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        async with uow.transactional() as session:
            await courier_service.process_webhook_event(
                session,
                provider_code=provider_code,
                body=body,
                headers=headers,
            )
    except IntegrationError as e:
        _logger.warning(
            "courier_webhook_integration_error",
            provider_code=provider_code,
            error=str(e),
        )
        return JSONResponse(
            {"received": True, "resolution": "not_configured"},
            status_code=200,
        )
    except Exception as e:  # noqa: BLE001
        _logger.error(
            "courier_webhook_unhandled",
            provider_code=provider_code,
            error=str(e),
        )
        return JSONResponse(
            {"received": True, "resolution": "deferred"},
            status_code=200,
        )

    return JSONResponse(
        {"received": True, "resolution": "processed"},
        status_code=200,
    )
