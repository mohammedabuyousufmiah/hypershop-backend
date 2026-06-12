"""Pydantic v2 schemas for the Sponsored Products module.

Money fields are integer minor units (paisa). API layer should not
divide on the wire — FE consumes /100 only at render time.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


CampaignStatus = Literal[
    "draft", "active", "paused", "ended", "budget_exhausted",
]
AdGroupStatus = Literal["active", "paused"]
TargetType = Literal["keyword", "category", "auto"]
Surface = Literal["search", "category", "pdp_related"]
WalletTxnKind = Literal[
    "recharge", "click_debit", "refund", "manual_credit", "adjust",
]


class AdTarget(BaseModel):
    """Single entry in an ad-group's targets JSONB array."""
    keyword: str | None = None
    category_slug: str | None = None
    bid_override_minor: int | None = Field(default=None, ge=200)


# ─── Wallet ─────────────────────────────────────────────────────────
class AdWalletBalanceRead(BaseModel):
    balance_minor: int
    total_spent_minor: int
    auto_recharge_enabled: bool
    auto_recharge_threshold_minor: int | None = None
    auto_recharge_amount_minor: int | None = None
    currency: str = "BDT"


class AdWalletRechargeRequest(BaseModel):
    amount_minor: int = Field(..., ge=10000, le=5000000)
    bkash_msisdn: str = Field(..., pattern=r"^\+?880?1[3-9]\d{8}$")


class AdWalletRechargeResponse(BaseModel):
    payment_id: str
    bkash_url: str
    expires_at: datetime


# ─── Phase 1.B persisted-recharge schemas ───────────────────────────
RechargeProvider = Literal["bkash", "sslcommerz", "fake"]
RechargeStatus = Literal[
    "initiated", "pending", "succeeded", "failed", "expired", "refunded",
]


class AdWalletRechargeInitiateRequest(BaseModel):
    amount_minor: int = Field(..., ge=10_000, le=5_000_000)
    provider: RechargeProvider = "bkash"


class AdWalletRechargeInitiateResponse(BaseModel):
    recharge_id: UUID
    redirect_url: str
    status: RechargeStatus


class AdWalletRechargeRead(BaseModel):
    id: UUID
    ad_wallet_id: UUID
    seller_id: UUID
    amount_minor: int
    provider: RechargeProvider
    provider_payment_id: str | None
    provider_transaction_id: str | None
    status: RechargeStatus
    failure_reason: str | None
    wallet_txn_id: UUID | None
    bkash_create_url: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class AdWalletTxnListResponse(BaseModel):
    items: list["AdWalletTxnRead"]
    total: int
    limit: int
    offset: int


class AutoRechargeUpdate(BaseModel):
    enabled: bool
    threshold_minor: int | None = Field(default=None, ge=10000)
    amount_minor: int | None = Field(default=None, ge=10000, le=5000000)


class AdminWalletCreditRequest(BaseModel):
    amount_minor: int = Field(..., ge=1)
    memo: str = Field(..., min_length=1, max_length=512)


class AdminWalletDebitRequest(BaseModel):
    amount_minor: int = Field(..., ge=1)
    memo: str = Field(..., min_length=1, max_length=512)


class AdminWalletView(BaseModel):
    wallet: "AdWalletRead"
    recent_txns: list["AdWalletTxnRead"]
    total_txns: int


class AdminWalletListItem(BaseModel):
    seller_id: UUID
    balance_minor: int
    total_spent_minor: int
    auto_recharge_enabled: bool


class AdminWalletListResponse(BaseModel):
    items: list[AdminWalletListItem]
    total: int
    limit: int
    offset: int


class AdWalletCreate(BaseModel):
    seller_id: UUID


class AdWalletUpdate(BaseModel):
    auto_recharge_enabled: bool | None = None
    auto_recharge_threshold_minor: int | None = Field(default=None, ge=0)
    auto_recharge_amount_minor: int | None = Field(default=None, ge=0)


class AdWalletRead(BaseModel):
    id: UUID
    seller_id: UUID
    balance_minor: int
    total_spent_minor: int
    auto_recharge_enabled: bool
    auto_recharge_threshold_minor: int | None
    auto_recharge_amount_minor: int | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AdWalletTxnCreate(BaseModel):
    ad_wallet_id: UUID
    kind: WalletTxnKind
    amount_minor: int
    source_type: str | None = None
    source_id: UUID | None = None
    memo: str | None = None


class AdWalletTxnUpdate(BaseModel):
    memo: str | None = None


class AdWalletTxnRead(BaseModel):
    id: UUID
    ad_wallet_id: UUID
    kind: WalletTxnKind
    amount_minor: int
    balance_after_minor: int
    source_type: str | None
    source_id: UUID | None
    memo: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ─── Campaign ───────────────────────────────────────────────────────
class AdCampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    daily_budget_minor: int = Field(ge=0)
    total_budget_minor: int | None = Field(default=None, ge=0)
    start_at: datetime | None = None
    end_at: datetime | None = None
    status: CampaignStatus = "draft"


class AdCampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    daily_budget_minor: int | None = Field(default=None, ge=0)
    total_budget_minor: int | None = Field(default=None, ge=0)
    start_at: datetime | None = None
    end_at: datetime | None = None
    status: CampaignStatus | None = None


class AdCampaignRead(BaseModel):
    id: UUID
    seller_id: UUID
    name: str
    status: CampaignStatus
    start_at: datetime | None
    end_at: datetime | None
    daily_budget_minor: int
    total_budget_minor: int | None
    total_spent_minor: int
    today_spent_minor: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ─── Ad Group ───────────────────────────────────────────────────────
class AdGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    default_bid_minor: int = Field(ge=200)
    target_type: TargetType
    targets: list[AdTarget]
    product_ids: list[UUID]
    status: AdGroupStatus = "active"


class AdGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    default_bid_minor: int | None = Field(default=None, ge=200)
    targets: list[AdTarget] | None = None
    product_ids: list[UUID] | None = None
    status: AdGroupStatus | None = None


class AdGroupRead(BaseModel):
    id: UUID
    campaign_id: UUID
    name: str
    status: AdGroupStatus
    default_bid_minor: int
    target_type: TargetType
    targets: list[AdTarget]
    product_ids: list[UUID]
    quality_score: float
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ─── Impression / Click / Quality ───────────────────────────────────
class AdImpressionCreate(BaseModel):
    ad_group_id: UUID
    product_id: UUID
    surface: Surface
    surface_ref: str = Field(min_length=1, max_length=256)
    slot_position: int = Field(ge=0)
    user_id: UUID | None = None
    session_id: str | None = Field(default=None, max_length=64)
    bid_at_auction_minor: int = Field(ge=0)
    cleared_price_minor: int = Field(ge=0)


class AdImpressionUpdate(BaseModel):
    # Impressions are append-only; PATCH supplied for symmetry, no-op for now.
    pass


class AdImpressionRead(BaseModel):
    id: int
    ad_group_id: UUID
    product_id: UUID
    surface: Surface
    surface_ref: str
    slot_position: int
    user_id: UUID | None
    session_id: str | None
    bid_at_auction_minor: int
    cleared_price_minor: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AdClickCreate(BaseModel):
    impression_id: int
    user_id: UUID | None = None


class AdClickUpdate(BaseModel):
    is_invalid: bool | None = None
    invalid_reason: str | None = Field(default=None, max_length=64)


class AdClickRead(BaseModel):
    id: int
    impression_id: int
    ad_group_id: UUID
    product_id: UUID
    seller_id: UUID
    user_id: UUID | None
    charged_amount_minor: int
    wallet_txn_id: UUID | None
    is_invalid: bool
    invalid_reason: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AdQualitySnapshotCreate(BaseModel):
    ad_group_id: UUID
    ctr: float | None = None
    rating_avg: float | None = None
    in_stock_rate: float | None = None
    computed_score: float | None = None


class AdQualitySnapshotUpdate(BaseModel):
    computed_score: float | None = None


class AdQualitySnapshotRead(BaseModel):
    id: int
    ad_group_id: UUID
    ctr: float | None
    rating_avg: float | None
    in_stock_rate: float | None
    computed_score: float | None
    computed_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ─── Auction result (Phase 1.D) ─────────────────────────────────────
class AuctionWinner(BaseModel):
    impression_id: int
    ad_group_id: UUID
    product_id: UUID
    seller_id: UUID
    slot_position: int
    cleared_price_minor: int
    bid_minor: int
    quality_score: float


class RecordClickRequest(BaseModel):
    impression_id: int
    user_id: UUID | None = None
    session_id: str | None = Field(default=None, max_length=64)


class RecordClickResponse(BaseModel):
    click_id: int
    charged_minor: int
    redirect_product_id: UUID
