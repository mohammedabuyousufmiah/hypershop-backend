"""ORM models for the sellers module — phase 1.

Two tables:

- ``sellers``       — one row per seller business. Includes BD-typical
                      KYC fields (TIN, NID, bank, trade license) all
                      nullable so legal can decide which are mandatory
                      at admin-review time. Commission + payout config
                      are per-row knobs the admin tunes.
- ``seller_users``  — N IAM ``users`` rows ↔ 1 ``sellers`` row, with
                      a role enum (owner / manager / staff). Composite
                      PK enforces "one user, one seller" — a future
                      phase can relax this to support agency-style
                      multi-seller managers if the business asks.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin
from app.modules.sellers.codes import (
    ALL_PAYOUT_CADENCES,
    ALL_PAYOUT_METHODS,
    ALL_SELLER_ROLES,
    ALL_STATUSES,
    DEFAULT_COMMISSION_PERCENT,
    DEFAULT_PAYOUT_CADENCE,
    DEFAULT_PAYOUT_METHOD,
    STATUS_REGISTERED,
)


def _check(values: tuple[str, ...], col: str) -> str:
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


class Seller(Base, TimestampMixin):
    __tablename__ = "sellers"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    business_name: Mapped[str] = mapped_column(
        String(length=200), nullable=False,
    )
    # Soft-handle for the seller in URLs / dashboards. Unique enforced
    # at the DB level so two sellers can't share a slug.
    slug: Mapped[str] = mapped_column(
        String(length=80), nullable=False, unique=True,
    )
    contact_email: Mapped[str | None] = mapped_column(
        String(length=320), nullable=True,
    )
    contact_phone: Mapped[str | None] = mapped_column(
        String(length=32), nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(length=24),
        nullable=False,
        server_default=STATUS_REGISTERED,
    )

    # ---- KYC fields (all nullable; admin decides required set) ----
    tin: Mapped[str | None] = mapped_column(String(length=32), nullable=True)
    nid: Mapped[str | None] = mapped_column(String(length=32), nullable=True)
    trade_license_no: Mapped[str | None] = mapped_column(
        String(length=64), nullable=True,
    )
    # Bank details are PII-adjacent; keep one row's worth here. A
    # future phase may move these to an encrypted side table.
    bank_account_name: Mapped[str | None] = mapped_column(
        String(length=200), nullable=True,
    )
    bank_account_number: Mapped[str | None] = mapped_column(
        String(length=32), nullable=True,
    )
    bank_name: Mapped[str | None] = mapped_column(
        String(length=120), nullable=True,
    )
    bank_branch: Mapped[str | None] = mapped_column(
        String(length=120), nullable=True,
    )

    # ---- commission + payout config (per-seller knobs) ----
    commission_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        server_default=text(f"{DEFAULT_COMMISSION_PERCENT}"),
    )
    payout_cadence: Mapped[str] = mapped_column(
        String(length=16),
        nullable=False,
        server_default=DEFAULT_PAYOUT_CADENCE,
    )
    payout_method: Mapped[str] = mapped_column(
        String(length=24),
        nullable=False,
        server_default=DEFAULT_PAYOUT_METHOD,
    )
    payout_account_id: Mapped[str | None] = mapped_column(
        String(length=64), nullable=True,
    )

    # ---- moderation trail ----
    rejection_reason: Mapped[str | None] = mapped_column(
        String(length=500), nullable=True,
    )
    moderated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    moderated_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(_check(ALL_STATUSES, "status"), name="ck_seller_status"),
        CheckConstraint(
            _check(ALL_PAYOUT_CADENCES, "payout_cadence"),
            name="ck_seller_payout_cadence",
        ),
        CheckConstraint(
            _check(ALL_PAYOUT_METHODS, "payout_method"),
            name="ck_seller_payout_method",
        ),
        CheckConstraint(
            "commission_percent BETWEEN 0 AND 100",
            name="ck_seller_commission_range",
        ),
        Index("ix_sellers_status", "status"),
    )


class SellerUser(Base, TimestampMixin):
    """Linkage row: one IAM user ↔ one seller, with a role on that seller."""

    __tablename__ = "seller_users"

    seller_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(length=16), nullable=False)

    __table_args__ = (
        CheckConstraint(
            _check(ALL_SELLER_ROLES, "role"),
            name="ck_seller_user_role",
        ),
        # One user = one seller for phase 1. Drop this in a later
        # phase if multi-seller managers become a use case.
        Index("uq_seller_user_user", "user_id", unique=True),
        Index("ix_seller_user_seller", "seller_id"),
    )
