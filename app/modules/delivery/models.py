"""Delivery zone model.

A ``DeliveryZone`` describes a price band for a set of addresses. The user-
defined business rules are:

- ``service_area`` zones (own delivery) → price 50 BDT.
- ``3pl`` zones (third-party logistics) → price 70–150 BDT.
- COD never adds a surcharge (``cod_fee = 0``).

Rule values are validated at the schema layer (allowing admin to extend
ranges later) and at the DB CHECK level for the ``3pl`` cap so a runaway
config can't push fees past 150.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class DeliveryZoneKind(StrEnum):
    SERVICE_AREA = "service_area"
    THIRD_PARTY = "3pl"


class DeliveryZone(Base, TimestampMixin):
    __tablename__ = "delivery_zones"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[DeliveryZoneKind] = mapped_column(String(16), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="BDT")
    cities: Mapped[list[str]] = mapped_column(
        ARRAY(String(120)),
        nullable=False,
        server_default=text("ARRAY[]::varchar[]"),
    )
    postal_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String(16)),
        nullable=False,
        server_default=text("ARRAY[]::varchar[]"),
    )
    is_default: Mapped[bool] = mapped_column(
        nullable=False,
        server_default=text("false"),
    )
    is_active: Mapped[bool] = mapped_column(
        nullable=False,
        server_default=text("true"),
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('service_area','3pl')",
            name="kind_enum",
        ),
        CheckConstraint("price >= 0", name="price_nonneg"),
        # Hard cap on 3PL fees per the user's stated rule. Service-area exact
        # value (50) is enforced at the schema layer to keep DB flexible if
        # the rule evolves to a regional override (e.g. Chattogram = 60).
        CheckConstraint(
            "kind <> '3pl' OR (price >= 70 AND price <= 150)",
            name="3pl_price_band",
        ),
        CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="currency_iso",
        ),
        Index("ix_delivery_zones_kind_active", "kind", "is_active"),
        Index("ix_delivery_zones_is_default", "is_default"),
    )
