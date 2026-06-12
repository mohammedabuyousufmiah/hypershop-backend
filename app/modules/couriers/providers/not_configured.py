"""Fail-loud fallback for any courier without active credentials."""
from __future__ import annotations

from app.core.errors import IntegrationError
from app.modules.couriers.providers.base import (
    CancelShipmentResult,
    CourierProvider,
    CourierWebhookEvent,
    CreateShipmentRequest,
    CreateShipmentResult,
    ShipmentStatusResult,
)


class NotConfiguredCourierProvider(CourierProvider):
    """Raises IntegrationError on every operation. Used when the
    operator has not yet supplied credentials for a courier in
    ``hypershop_courier_credentials``."""

    def __init__(self, code: str) -> None:
        self.code = code

    async def create_shipment(
        self, req: CreateShipmentRequest,
    ) -> CreateShipmentResult:
        raise IntegrationError(
            f"Courier '{self.code}' not configured. "
            "Add credentials in /admin/couriers.",
            details={"provider": self.code, "operation": "create_shipment"},
        )

    async def get_status(
        self, provider_shipment_id: str,
    ) -> ShipmentStatusResult:
        raise IntegrationError(
            f"Courier '{self.code}' not configured.",
            details={"provider": self.code, "operation": "get_status"},
        )

    async def cancel(
        self, provider_shipment_id: str, reason: str | None = None,
    ) -> CancelShipmentResult:
        raise IntegrationError(
            f"Courier '{self.code}' not configured.",
            details={"provider": self.code, "operation": "cancel"},
        )

    def parse_webhook(
        self, *, body: bytes, headers: dict[str, str],
    ) -> CourierWebhookEvent:
        raise IntegrationError(
            f"Courier '{self.code}' not configured.",
            details={"provider": self.code, "operation": "parse_webhook"},
        )
