"""Sponsored Products ORM mappings.

7 tables: ad wallet + wallet txns + campaigns + ad groups + impressions
+ clicks + quality snapshots. All prefixed ``hypershop_ad_``.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class HypershopAdWallet(Base):
    __tablename__ = "hypershop_ad_wallets"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    seller_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    balance_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"),
    )
    total_spent_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"),
    )
    auto_recharge_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    auto_recharge_threshold_minor: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
    )
    auto_recharge_amount_minor: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "balance_minor >= 0",
            name="ck_hypershop_ad_wallets_balance_nonneg",
        ),
    )


class HypershopAdWalletTxn(Base):
    __tablename__ = "hypershop_ad_wallet_txns"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    ad_wallet_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_ad_wallets.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # Signed: positive = credit, negative = debit. No >0 check.
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    source_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    memo: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "balance_after_minor >= 0",
            name="ck_hypershop_ad_wallet_txns_balance_nonneg",
        ),
        CheckConstraint(
            "kind IN ('recharge','click_debit','refund','manual_credit','adjust')",
            name="ck_hypershop_ad_wallet_txns_kind",
        ),
        Index(
            "ix_hypershop_ad_wallet_txns_wallet_at",
            "ad_wallet_id",
            text("created_at DESC"),
        ),
    )


class HypershopAdCampaign(Base):
    __tablename__ = "hypershop_ad_campaigns"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    seller_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'draft'"),
    )
    start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    daily_budget_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_budget_minor: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
    )
    total_spent_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"),
    )
    today_spent_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','active','paused','ended','budget_exhausted')",
            name="ck_hypershop_ad_campaigns_status",
        ),
        CheckConstraint(
            "daily_budget_minor >= 0",
            name="ck_hypershop_ad_campaigns_daily_budget_nonneg",
        ),
        Index(
            "ix_hypershop_ad_campaigns_seller_status",
            "seller_id", "status",
        ),
        Index(
            "ix_hypershop_ad_campaigns_active_end",
            "status", "end_at",
            postgresql_where=text("status = 'active'"),
        ),
    )


class HypershopAdGroup(Base):
    __tablename__ = "hypershop_ad_groups"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    campaign_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_ad_campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'active'"),
    )
    default_bid_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    targets: Mapped[list] = mapped_column(JSONB, nullable=False)
    product_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PgUUID(as_uuid=True)), nullable=False,
    )
    quality_score: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, server_default=text("1.00"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','paused')",
            name="ck_hypershop_ad_groups_status",
        ),
        CheckConstraint(
            "default_bid_minor >= 200",
            name="ck_hypershop_ad_groups_min_bid",
        ),
        CheckConstraint(
            "target_type IN ('keyword','category','auto')",
            name="ck_hypershop_ad_groups_target_type",
        ),
        CheckConstraint(
            "quality_score >= 0.50 AND quality_score <= 1.50",
            name="ck_hypershop_ad_groups_quality_range",
        ),
        Index(
            "ix_hypershop_ad_groups_targets_gin",
            "targets",
            postgresql_using="gin",
        ),
        Index(
            "ix_hypershop_ad_groups_campaign_status",
            "campaign_id", "status",
        ),
    )


class HypershopAdImpression(Base):
    __tablename__ = "hypershop_ad_impressions"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True,
    )
    ad_group_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_ad_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False,
    )
    surface: Mapped[str] = mapped_column(String(16), nullable=False)
    surface_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    slot_position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bid_at_auction_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cleared_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "surface IN ('search','category','pdp_related')",
            name="ck_hypershop_ad_impressions_surface",
        ),
        Index(
            "ix_hypershop_ad_impressions_group_at",
            "ad_group_id", text("created_at DESC"),
        ),
        Index(
            "ix_hypershop_ad_impressions_surface_ref_at",
            "surface", "surface_ref", text("created_at DESC"),
        ),
    )


class HypershopAdClick(Base):
    __tablename__ = "hypershop_ad_clicks"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True,
    )
    impression_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("hypershop_ad_impressions.id", ondelete="CASCADE"),
        nullable=False,
    )
    ad_group_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_ad_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False,
    )
    seller_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False,
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    charged_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    wallet_txn_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_ad_wallet_txns.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_invalid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    invalid_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        Index(
            "ix_hypershop_ad_clicks_seller_at",
            "seller_id", text("created_at DESC"),
        ),
        # Each valid click maps to exactly one impression; refunded /
        # invalid duplicates may exist for the same impression.
        Index(
            "uq_hypershop_ad_clicks_impression_valid",
            "impression_id",
            unique=True,
            postgresql_where=text("is_invalid = false"),
        ),
    )


class HypershopAdQualitySnapshot(Base):
    __tablename__ = "hypershop_ad_quality_snapshots"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True,
    )
    ad_group_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_ad_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    ctr: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    rating_avg: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    in_stock_rate: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    computed_score: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        Index(
            "ix_hypershop_ad_quality_snapshots_group_at",
            "ad_group_id", text("computed_at DESC"),
        ),
    )


class HypershopAdWalletRecharge(Base):
    """Phase 1.B — persisted provider-checkout sessions for wallet top-ups.

    One row per Bkash (or other provider) recharge attempt. ``status``
    transitions: ``initiated`` (row written, provider call pending) →
    ``pending`` (provider returned a ``provider_payment_id`` and the
    seller has been redirected) → ``succeeded`` / ``failed`` / ``expired``.
    The ``provider_payment_id`` UNIQUE constraint + the status check in
    :func:`app.modules.ads.service.finalize_recharge_from_callback` is the
    end-to-end idempotency contract — Bkash may re-deliver callbacks
    freely without crediting the wallet twice.
    """

    __tablename__ = "hypershop_ad_wallet_recharges"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    ad_wallet_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_ad_wallets.id", ondelete="CASCADE"),
        nullable=False,
    )
    seller_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        nullable=False,
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_payment_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
    )
    provider_transaction_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    wallet_txn_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_ad_wallet_txns.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    bkash_create_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "amount_minor >= 10000",
            name="ck_hypershop_ad_wallet_recharges_min_amount",
        ),
        CheckConstraint(
            "provider IN ('bkash','sslcommerz','fake')",
            name="ck_hypershop_ad_wallet_recharges_provider",
        ),
        CheckConstraint(
            "status IN ('initiated','pending','succeeded','failed','expired','refunded')",
            name="ck_hypershop_ad_wallet_recharges_status",
        ),
        UniqueConstraint(
            "provider_payment_id",
            name="uq_hypershop_ad_wallet_recharges_provider_payment_id",
        ),
        Index(
            "ix_hypershop_ad_wallet_recharges_seller_at",
            "seller_id", text("created_at DESC"),
        ),
        Index(
            "ix_hypershop_ad_wallet_recharges_status_at",
            "status", "created_at",
            postgresql_where=text("status IN ('initiated','pending')"),
        ),
    )


__all__ = [
    "HypershopAdWallet",
    "HypershopAdWalletTxn",
    "HypershopAdWalletRecharge",
    "HypershopAdCampaign",
    "HypershopAdGroup",
    "HypershopAdImpression",
    "HypershopAdClick",
    "HypershopAdQualitySnapshot",
    # Belt-and-suspenders aliases — UniqueConstraint module-level keep
    # SQLAlchemy import warnings quiet on PG-specific dialects.
]


# Suppress unused-import lints (the import is needed for ``UniqueConstraint``
# resolution if a future migration adds a uniqueness rule inline).
_ = UniqueConstraint
