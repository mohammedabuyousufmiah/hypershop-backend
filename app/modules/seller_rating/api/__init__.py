"""Seller rating API — admin + public routers."""
from app.modules.seller_rating.api.admin import router as admin_router
from app.modules.seller_rating.api.public import router as public_router

__all__ = ["admin_router", "public_router"]
