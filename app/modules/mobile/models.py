"""Mobile-app support tables.

- :class:`DeviceToken` — push-notification token registration. The
  notifications module (when wired to FCM/APNs) reads this table to
  fan out reminder + order-status pushes. UNIQUE on (user_id, token)
  so re-registering the same handset upserts cleanly.
- :class:`CustomerAddress` — saved delivery addresses for the customer
  app's checkout pre-fill. One default per user enforced via partial
  unique index in the migration.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class DeviceToken(Base, TimestampMixin):
    __tablename__ = "device_tokens"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    token: Mapped[str] = mapped_column(String(512), nullable=False)
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    locale: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    is_active: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true"),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "token", name="uq_device_tokens_user_token"),
        CheckConstraint(
            "kind IN ('fcm','apns','web')", name="kind_enum",
        ),
        Index("ix_device_tokens_user_active", "user_id", "is_active"),
        Index("ix_device_tokens_token", "token"),
    )


class CustomerAddress(Base, TimestampMixin):
    __tablename__ = "customer_addresses"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    customer_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient_name: Mapped[str] = mapped_column(String(160), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    line1: Mapped[str] = mapped_column(String(255), nullable=False)
    line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(96), nullable=False)
    district: Mapped[str | None] = mapped_column(String(96), nullable=True)
    division: Mapped[str | None] = mapped_column(String(96), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    country: Mapped[str] = mapped_column(
        String(2), nullable=False, server_default="BD",
    )
    is_default: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false"),
    )

    __table_args__ = (
        Index("ix_customer_addresses_user", "customer_user_id"),
        # The migration adds:
        #   CREATE UNIQUE INDEX uq_customer_addresses_one_default
        #   ON customer_addresses (customer_user_id) WHERE is_default = true
        # so a user can have at most one default address.
    )


class CustomerPreferences(Base, TimestampMixin):
    """Per-customer preferences (locale/currency + marketing opt-ins +
    preferred categories). One row per user; auto-created on first read.
    Backs GET/PATCH /api/v1/customers/preferences (migration 0096)."""

    __tablename__ = "customer_preferences"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    locale: Mapped[str] = mapped_column(String(16), nullable=False, server_default="en-BD")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, server_default="BDT")
    email_marketing: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    sms_marketing: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    push_marketing: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    preferred_categories: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )
