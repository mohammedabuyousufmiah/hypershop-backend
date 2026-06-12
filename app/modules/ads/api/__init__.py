"""Ads API package — seller, admin, public, and webhook surfaces."""
from app.modules.ads.api.admin import router as admin_router
from app.modules.ads.api.public import router as public_router
from app.modules.ads.api.seller import router as seller_router
from app.modules.ads.api.webhooks import router as webhook_router

__all__ = ["seller_router", "admin_router", "public_router", "webhook_router"]
