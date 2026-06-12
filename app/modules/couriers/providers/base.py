"""Abstract :class:`CourierProvider` port + DTOs.

Every courier adapter implements this interface. The service layer
calls the bound provider via ``providers.get_provider()`` and never
touches adapter-specific logic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class CreateShipmentRequest:
    """Order-shaped request the service hands to a courier adapter."""

    order_id: str
    pickup_address: dict   # {name, phone, address, city, area, postal_code}
    delivery_address: dict
    items: list[dict]      # [{name, qty, weight_grams, value_minor}]
    cod_amount_minor: int  # 0 if prepaid
    service_type: str      # "regular" | "express" | "same_day" | "next_day"
    metadata: dict | None = None


@dataclass(slots=True)
class CreateShipmentResult:
    """What the courier returns from "create a shipment"."""

    provider_shipment_id: str
    tracking_number: str | None
    label_url: str | None
    cost_minor: int          # shipping_charge as quoted by provider
    estimated_delivery_at: str | None  # ISO timestamp
    raw: dict


@dataclass(slots=True)
class ShipmentStatusResult:
    """Snapshot of a single shipment's state at the courier."""

    status: str              # mapped (use codes.STATUS_*)
    provider_status: str     # raw provider status string
    last_event_at: str | None
    raw: dict


@dataclass(slots=True)
class CancelShipmentResult:
    cancelled: bool
    reason: str | None
    raw: dict


@dataclass(slots=True)
class CourierWebhookEvent:
    """What the adapter parses out of a raw webhook body."""

    provider_shipment_id: str
    event_type: str
    mapped_status: str | None
    raw: dict


class CourierProvider(ABC):
    """Capability port. Adapters implement against their REST API."""

    code: str = ""  # subclass sets this

    @abstractmethod
    async def create_shipment(
        self, req: CreateShipmentRequest,
    ) -> CreateShipmentResult: ...

    @abstractmethod
    async def get_status(
        self, provider_shipment_id: str,
    ) -> ShipmentStatusResult: ...

    @abstractmethod
    async def cancel(
        self, provider_shipment_id: str, reason: str | None = None,
    ) -> CancelShipmentResult: ...

    @abstractmethod
    def parse_webhook(
        self, *, body: bytes, headers: dict[str, str],
    ) -> CourierWebhookEvent: ...
