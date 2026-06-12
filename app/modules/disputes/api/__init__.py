"""Disputes API package — buyer + seller + admin surfaces."""
from app.modules.disputes.api.admin import router as admin_router
from app.modules.disputes.api.buyer import router as buyer_router
from app.modules.disputes.api.seller import router as seller_router

__all__ = ["buyer_router", "seller_router", "admin_router"]
