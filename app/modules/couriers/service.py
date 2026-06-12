"""Thin courier service layer.

For M2.A most operations just delegate to repo + ``get_provider``.
M2.B will swap NotConfiguredCourierProvider with real adapters via
``providers.register_provider``.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.couriers import repository as repo
from app.modules.couriers.codes import (
    LIVE_STATUSES,
    STATUS_CANCELLED,
    STATUS_CREATED,
    TERMINAL_STATUSES,
)
from app.modules.couriers.providers import get_provider
from app.modules.couriers.providers.base import CreateShipmentRequest


class CourierNotEnabled(Exception):
    """Raised when an operation targets a disabled provider."""


class ShipmentAlreadyExists(Exception):
    """Raised when an order already has a live shipment."""


class ShipmentNotFound(Exception):
    """Raised when a shipment id does not resolve."""


async def create_shipment(
    session: AsyncSession,
    *,
    order_id: UUID,
    provider_code: str,
    service_type: str,
    is_cod: bool,
    cod_amount_minor: int,
    pickup_address: dict,
    delivery_address: dict,
    items: list[dict],
) -> dict:
    """End-to-end shipment creation: verify provider, dedupe live
    shipment for the order, call provider, persist row."""
    provider_row = await repo.get_provider(session, provider_code)
    if provider_row is None or not provider_row.is_enabled:
        raise CourierNotEnabled(f"Courier '{provider_code}' not enabled.")

    existing = await repo.list_shipments_for_order(session, order_id)
    for s in existing:
        if s.status in LIVE_STATUSES:
            raise ShipmentAlreadyExists(
                f"Order {order_id} already has a live shipment "
                f"({s.provider_code} / {s.provider_shipment_id}).",
            )

    provider = get_provider(provider_code)
    # TODO(M2.B): real HTTP call to courier; today this raises
    # IntegrationError from NotConfiguredCourierProvider.
    result = await provider.create_shipment(
        CreateShipmentRequest(
            order_id=str(order_id),
            pickup_address=pickup_address,
            delivery_address=delivery_address,
            items=items,
            cod_amount_minor=cod_amount_minor,
            service_type=service_type,
            metadata=None,
        )
    )

    shipment = await repo.create_shipment_row(
        session,
        order_id=order_id,
        provider_code=provider_code,
        provider_shipment_id=result.provider_shipment_id,
        tracking_number=result.tracking_number,
        label_url=result.label_url,
        service_type=service_type,
        is_cod=is_cod,
        cod_amount_minor=cod_amount_minor,
        shipping_charge_minor=result.cost_minor,
        pickup_address=pickup_address,
        delivery_address=delivery_address,
        provider_response=result.raw,
        status=STATUS_CREATED,
    )
    return {
        "shipment_id": str(shipment.id),
        "tracking_number": result.tracking_number,
        "label_url": result.label_url,
        "estimated_delivery_at": result.estimated_delivery_at,
    }


async def refresh_status(
    session: AsyncSession, shipment_id: UUID,
) -> dict:
    """Pull current status from the provider and persist the event."""
    shipment = await repo.get_shipment(session, shipment_id)
    if shipment is None:
        raise ShipmentNotFound(f"Shipment {shipment_id} not found.")
    if shipment.provider_shipment_id is None:
        return {"status": shipment.status, "no_provider_id": True}
    provider = get_provider(shipment.provider_code)
    # TODO(M2.B): real HTTP call to courier status endpoint.
    result = await provider.get_status(shipment.provider_shipment_id)
    await repo.record_status_event(
        session,
        shipment_id=shipment.id,
        provider_code=shipment.provider_code,
        event_type=result.provider_status,
        mapped_status=result.status,
        raw_payload=result.raw,
        provider_shipment_id=shipment.provider_shipment_id,
    )
    await repo.update_shipment(session, shipment.id, status=result.status)
    return {"status": result.status, "raw_status": result.provider_status}


async def cancel_shipment(
    session: AsyncSession,
    shipment_id: UUID,
    reason: str | None = None,
) -> dict:
    shipment = await repo.get_shipment(session, shipment_id)
    if shipment is None:
        raise ShipmentNotFound(f"Shipment {shipment_id} not found.")
    if shipment.status in TERMINAL_STATUSES:
        return {"already_terminal": True, "status": shipment.status}
    provider = get_provider(shipment.provider_code)
    # TODO(M2.B): real HTTP call to courier cancel endpoint.
    cancel = await provider.cancel(shipment.provider_shipment_id, reason)
    if cancel.cancelled:
        await repo.update_shipment(
            session, shipment.id, status=STATUS_CANCELLED,
        )
    return {"cancelled": cancel.cancelled, "reason": cancel.reason}


async def process_webhook_event(
    session: AsyncSession,
    *,
    provider_code: str,
    body: bytes,
    headers: dict[str, str],
) -> None:
    """Parse the webhook + log event + update shipment row."""
    provider = get_provider(provider_code)
    # TODO(M2.B): real signature-verify + payload parse.
    event = provider.parse_webhook(body=body, headers=headers)
    shipment = await repo.get_shipment_by_provider_id(
        session, provider_code, event.provider_shipment_id,
    )
    await repo.record_status_event(
        session,
        shipment_id=(shipment.id if shipment else None),
        provider_code=provider_code,
        event_type=event.event_type,
        mapped_status=event.mapped_status,
        raw_payload=event.raw,
        provider_shipment_id=event.provider_shipment_id,
    )
    if shipment and event.mapped_status:
        await repo.update_shipment(
            session, shipment.id, status=event.mapped_status,
        )
