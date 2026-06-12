"""Delivery pricing service.

Public API
----------
- :meth:`DeliveryService.quote` — given an address + payment method, return
  the matching zone's price plus any surcharges.
- :meth:`DeliveryService.create_zone` / :meth:`update_zone` / :meth:`delete_zone`
  — admin CRUD for the rate table.

Matching algorithm (first-match-wins)
-------------------------------------
1. Postal-code exact match across active zones (most specific).
2. City case-insensitive match across active zones.
3. The single ``is_default`` active zone.
4. If none → :class:`NotFoundError` ("no delivery available").

This deterministic ordering means an admin can override a regional 3PL fee
for a specific postal code by creating a more-specific zone — the matcher
will pick it up before falling back to the city-level rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.errors import BusinessRuleError, NotFoundError
from app.core.security.principal import Principal
from app.modules.delivery.models import DeliveryZone, DeliveryZoneKind
from app.modules.delivery.repository import DeliveryZoneRepository
from app.modules.delivery.schemas import _enforce_kind_price


@dataclass(frozen=True, slots=True)
class DeliveryQuote:
    zone_code: str
    zone_name: str
    kind: str
    base_fee: Decimal
    cod_fee: Decimal
    total: Decimal
    currency: str


_ZERO = Decimal("0.00")


class DeliveryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.zones = DeliveryZoneRepository(session)

    async def quote(
        self,
        *,
        city: str,
        postal_code: str | None,
        payment_method: str,
    ) -> DeliveryQuote:
        zone = await self._match_zone(city=city, postal_code=postal_code)
        if zone is None:
            raise NotFoundError(
                "No delivery available to this address.",
                details={"city": city, "postal_code": postal_code},
            )

        # COD surcharge per current business rule = 0. Surfaced as a separate
        # field so callers (and the order summary) can show it explicitly,
        # and so changing the rule later is a one-line edit.
        cod_fee = _ZERO
        _ = payment_method  # parameter kept for forward-compat
        total = zone.price + cod_fee
        return DeliveryQuote(
            zone_code=zone.code,
            zone_name=zone.name,
            kind=zone.kind,
            base_fee=zone.price,
            cod_fee=cod_fee,
            total=total,
            currency=zone.currency,
        )

    async def _match_zone(
        self,
        *,
        city: str,
        postal_code: str | None,
    ) -> DeliveryZone | None:
        # Delegated to the repository so the postal/city/default
        # matching logic lives in a single SQL-backed implementation
        # (avoids loading every zone into Python on every quote call).
        return await self.zones.find_for_address(
            city=city, postal_code=postal_code,
        )

    # ---------------- Admin CRUD ----------------

    async def create_zone(
        self,
        *,
        principal: Principal,
        **fields: Any,
    ) -> DeliveryZone:
        # Defence-in-depth — schema layer enforces the rule, this catches
        # internal callers that bypass schemas.
        _enforce_kind_price(fields["kind"], fields["price"])
        zone = await self.zones.create(**fields)
        await record_audit(
            actor=principal,
            action="delivery.zone.create",
            resource_type="delivery_zone",
            resource_id=zone.id,
            metadata={
                "code": zone.code,
                "kind": zone.kind,
                "price": str(zone.price),
            },
        )
        return zone

    async def update_zone(
        self,
        *,
        principal: Principal,
        zone_id: UUID,
        **fields: Any,
    ) -> DeliveryZone:
        existing = await self.zones.get(zone_id)
        if existing is None:
            raise NotFoundError("Delivery zone not found.")
        new_kind = fields.get("kind") or existing.kind
        new_price = fields.get("price") if fields.get("price") is not None else existing.price
        try:
            _enforce_kind_price(new_kind, new_price)
        except ValueError as e:
            raise BusinessRuleError(str(e)) from e
        zone = await self.zones.update(zone_id, **fields)
        await record_audit(
            actor=principal,
            action="delivery.zone.update",
            resource_type="delivery_zone",
            resource_id=zone_id,
            metadata={"changed": [k for k, v in fields.items() if v is not None]},
        )
        return zone

    async def delete_zone(
        self,
        *,
        principal: Principal,
        zone_id: UUID,
    ) -> None:
        await self.zones.delete(zone_id)
        await record_audit(
            actor=principal,
            action="delivery.zone.delete",
            resource_type="delivery_zone",
            resource_id=zone_id,
        )


__all__ = ["DeliveryQuote", "DeliveryService", "DeliveryZoneKind"]
