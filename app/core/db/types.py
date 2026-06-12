from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from sqlalchemy import DateTime, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import mapped_column

UuidPK = Annotated[
    UUID,
    mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
]

UuidFK = Annotated[UUID, mapped_column(PG_UUID(as_uuid=True))]

TZAwareDateTime = Annotated[datetime, mapped_column(DateTime(timezone=True))]

MoneyAmount = Annotated[Decimal, mapped_column(Numeric(14, 2))]

CurrencyCode = Annotated[str, mapped_column(String(3))]

JsonB = Annotated[dict, mapped_column(JSONB, server_default=text("'{}'::jsonb"))]
