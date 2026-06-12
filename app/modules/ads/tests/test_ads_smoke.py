"""Ads module Phase 1.A smoke — imports + router registration."""
from __future__ import annotations


def test_ads_module_imports() -> None:
    from app.modules.ads.api import (
        admin_router,
        public_router,
        seller_router,
    )
    from app.modules.ads.models import (
        HypershopAdCampaign,
        HypershopAdClick,
        HypershopAdGroup,
        HypershopAdImpression,
        HypershopAdQualitySnapshot,
        HypershopAdWallet,
        HypershopAdWalletTxn,
    )
    assert all([
        HypershopAdWallet,
        HypershopAdWalletTxn,
        HypershopAdCampaign,
        HypershopAdGroup,
        HypershopAdImpression,
        HypershopAdClick,
        HypershopAdQualitySnapshot,
        seller_router,
        admin_router,
        public_router,
    ])
