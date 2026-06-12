"""Module-level constants for the Sponsored Products module."""
from __future__ import annotations


# Money (paisa, 1 BDT = 100).
MIN_BID_MINOR: int = 200          # ৳2.00 floor per click bid
MIN_RECHARGE_MINOR: int = 10_000  # ৳100.00 minimum wallet top-up

# Quality scoring.
DEFAULT_QUALITY_SCORE: float = 1.00
MIN_QUALITY_SCORE: float = 0.50
MAX_QUALITY_SCORE: float = 1.50

# Auction surfaces — where sponsored slots render.
SURFACE_SEARCH: str = "search"
SURFACE_CATEGORY: str = "category"
SURFACE_PDP_RELATED: str = "pdp_related"
ALL_SURFACES: tuple[str, ...] = (
    SURFACE_SEARCH, SURFACE_CATEGORY, SURFACE_PDP_RELATED,
)

# Campaign lifecycle.
CAMPAIGN_STATUS_DRAFT: str = "draft"
CAMPAIGN_STATUS_ACTIVE: str = "active"
CAMPAIGN_STATUS_PAUSED: str = "paused"
CAMPAIGN_STATUS_ENDED: str = "ended"
CAMPAIGN_STATUS_BUDGET_EXHAUSTED: str = "budget_exhausted"
ALL_CAMPAIGN_STATUSES: tuple[str, ...] = (
    CAMPAIGN_STATUS_DRAFT,
    CAMPAIGN_STATUS_ACTIVE,
    CAMPAIGN_STATUS_PAUSED,
    CAMPAIGN_STATUS_ENDED,
    CAMPAIGN_STATUS_BUDGET_EXHAUSTED,
)

# Ad group lifecycle (narrower than campaign — no draft/ended).
AD_GROUP_STATUS_ACTIVE: str = "active"
AD_GROUP_STATUS_PAUSED: str = "paused"
ALL_AD_GROUP_STATUSES: tuple[str, ...] = (
    AD_GROUP_STATUS_ACTIVE, AD_GROUP_STATUS_PAUSED,
)

# Targeting strategies.
TARGET_TYPE_KEYWORD: str = "keyword"
TARGET_TYPE_CATEGORY: str = "category"
TARGET_TYPE_AUTO: str = "auto"
ALL_TARGET_TYPES: tuple[str, ...] = (
    TARGET_TYPE_KEYWORD, TARGET_TYPE_CATEGORY, TARGET_TYPE_AUTO,
)

# Wallet txn kinds — signed amount_minor (positive = credit, negative = debit).
WALLET_TXN_KIND_RECHARGE: str = "recharge"
WALLET_TXN_KIND_CLICK_DEBIT: str = "click_debit"
WALLET_TXN_KIND_REFUND: str = "refund"
WALLET_TXN_KIND_MANUAL_CREDIT: str = "manual_credit"
WALLET_TXN_KIND_ADJUST: str = "adjust"
ALL_WALLET_TXN_KINDS: tuple[str, ...] = (
    WALLET_TXN_KIND_RECHARGE,
    WALLET_TXN_KIND_CLICK_DEBIT,
    WALLET_TXN_KIND_REFUND,
    WALLET_TXN_KIND_MANUAL_CREDIT,
    WALLET_TXN_KIND_ADJUST,
)

# Permission strings (mirrored in app/modules/iam/permissions.py).
PERM_ADS_VIEW: str = "ads.view"
PERM_ADS_MANAGE: str = "ads.manage"
